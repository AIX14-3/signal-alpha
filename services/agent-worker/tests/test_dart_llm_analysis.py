import unittest
import urllib.error
from datetime import date
from unittest.mock import patch

from app.analyzers.dart.llm import (
    DartLlmAnalyzer,
    DartLlmAnalysisError,
    GeminiGenerateContentClient,
    OpenAiChatClient,
    should_use_dart_llm,
)
from app.analyzers.dart.source_result import build_dart_analysis_result


class FakeLlmClient:
    def __init__(self, response: str):
        self.response = response
        self.prompts = []

    async def complete(self, *, prompt: str, model: str, timeout_seconds: float) -> str:
        self.prompts.append(
            {
                "prompt": prompt,
                "model": model,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


class FakeHttpResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        import json

        return json.dumps(self.payload).encode("utf-8")


class DartLlmAnalyzerTest(unittest.IsolatedAsyncioTestCase):
    async def test_analyzer_parses_valid_json_result(self):
        evidence_text = (
            "<DOCUMENT><COVER>cover metadata</COVER>"
            + (" filler" * 1200)
            + " Revenue 100 billion KRW. Operating profit 20 billion KRW. Net income 12 billion KRW."
            "</DOCUMENT>"
        )
        rule_result = build_dart_analysis_result(
            [
                {
                    "id": 10,
                    "event_type": "quarter_report",
                    "event_date": date(2026, 5, 15),
                    "signal_direction": "neutral",
                    "impact_level": "medium",
                    "title": "Quarterly report",
                    "summary": "DART disclosure: Quarterly report",
                    "evidence_text": evidence_text,
                    "needs_review": False,
                }
            ]
        )
        client = FakeLlmClient(
            """
            {
              "direction": "positive",
              "score": 72,
              "summary": "Revenue and operating profit improved based on the disclosure.",
              "key_facts": ["Revenue increased", "Operating profit improved"],
              "risk_flags": [],
              "needs_review": false,
              "confidence": 84
            }
            """
        )
        analyzer = DartLlmAnalyzer(client=client, model="test-model")

        result = await analyzer.analyze(
            events=[
                {
                    "id": 10,
                    "event_type": "quarter_report",
                    "title": "Quarterly report",
                    "evidence_text": evidence_text,
                }
            ],
            rule_result=rule_result,
            stock_code="005930",
        )

        self.assertEqual(result.direction, "positive")
        self.assertEqual(result.score, 72)
        self.assertEqual(result.confidence, 84)
        self.assertEqual(result.key_facts, ["Revenue increased", "Operating profit improved"])
        self.assertIn("JSON only", client.prompts[0]["prompt"])
        self.assertIn("financial_metrics", client.prompts[0]["prompt"])
        self.assertIn("dart_revenue", client.prompts[0]["prompt"])
        self.assertIn("Operating profit 20 billion KRW", client.prompts[0]["prompt"])
        self.assertNotIn("<DOCUMENT>", client.prompts[0]["prompt"])

    async def test_analyzer_normalizes_fractional_confidence_to_percent(self):
        rule_result = build_dart_analysis_result([])
        analyzer = DartLlmAnalyzer(
            client=FakeLlmClient(
                '{"direction":"neutral","score":50,"summary":"No material change was identified.","key_facts":[],"risk_flags":[],"needs_review":false,"confidence":0.82}'
            ),
            model="test-model",
        )

        result = await analyzer.analyze(events=[], rule_result=rule_result, stock_code="005930")

        self.assertEqual(result.confidence, 82)

    async def test_analyzer_rejects_invalid_direction(self):
        rule_result = build_dart_analysis_result([])
        analyzer = DartLlmAnalyzer(
            client=FakeLlmClient(
                '{"direction":"buy","score":99,"summary":"Buy now","key_facts":[],"risk_flags":[],"needs_review":false,"confidence":90}'
            ),
            model="test-model",
        )

        with self.assertRaises(DartLlmAnalysisError):
            await analyzer.analyze(events=[], rule_result=rule_result, stock_code="005930")

    async def test_analyzer_rejects_investment_advice_language(self):
        rule_result = build_dart_analysis_result([])
        analyzer = DartLlmAnalyzer(
            client=FakeLlmClient(
                '{"direction":"positive","score":80,"summary":"Buy this stock now.","key_facts":[],"risk_flags":[],"needs_review":false,"confidence":90}'
            ),
            model="test-model",
        )

        with self.assertRaises(DartLlmAnalysisError):
            await analyzer.analyze(events=[], rule_result=rule_result, stock_code="005930")

    async def test_analyzer_allows_non_advice_holding_language(self):
        rule_result = build_dart_analysis_result([])
        analyzer = DartLlmAnalyzer(
            client=FakeLlmClient(
                '{"direction":"neutral","score":50,"summary":"The disclosure describes shareholder holdings and governance information.","key_facts":["Shareholder holdings were disclosed"],"risk_flags":[],"needs_review":false,"confidence":80}'
            ),
            model="test-model",
        )

        result = await analyzer.analyze(events=[], rule_result=rule_result, stock_code="005930")

        self.assertEqual(result.direction, "neutral")

    def test_should_use_llm_only_for_high_impact_dart_events(self):
        self.assertTrue(
            should_use_dart_llm(
                [
                    {
                        "event_type": "quarter_report",
                        "impact_level": "medium",
                    }
                ],
                high_impact_only=True,
            )
        )
        self.assertTrue(
            should_use_dart_llm(
                [
                    {
                        "event_type": "governance_report",
                        "impact_level": "medium",
                    }
                ],
                high_impact_only=True,
            )
        )
        self.assertTrue(
            should_use_dart_llm(
                [
                    {
                        "event_type": "material_event",
                        "impact_level": "high",
                    }
                ],
                high_impact_only=True,
            )
        )
        self.assertFalse(
            should_use_dart_llm(
                [
                    {
                        "event_type": "insider_ownership",
                        "impact_level": "low",
                    }
                ],
                high_impact_only=True,
            )
        )

    async def test_openai_client_includes_error_body_detail_for_429(self):
        error_body = (
            b'{"error":{"message":"You exceeded your current quota.",'
            b'"type":"insufficient_quota","code":"insufficient_quota"}}'
        )
        http_error = urllib.error.HTTPError(
            url="https://api.openai.example/v1/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=None,
        )
        http_error.read = lambda: error_body
        client = OpenAiChatClient(api_key="test-key", base_url="https://api.openai.example/v1")

        with patch("urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(DartLlmAnalysisError) as context:
                await client.complete(prompt="{}", model="gpt-4o-mini", timeout_seconds=1)

        message = str(context.exception)
        self.assertIn("HTTP 429", message)
        self.assertIn("insufficient_quota", message)
        self.assertIn("You exceeded your current quota.", message)

    async def test_gemini_client_calls_generate_content_and_returns_text(self):
        captured = {}

        def fake_urlopen(request, timeout):
            import json

            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeHttpResponse(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": '{"direction":"neutral","score":50,"summary":"ok","key_facts":[],"risk_flags":[],"needs_review":false,"confidence":80}'
                                    }
                                ]
                            }
                        }
                    ]
                }
            )

        client = GeminiGenerateContentClient(
            api_key="gemini-test-key",
            base_url="https://generativelanguage.googleapis.com/v1beta",
        )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            response = await client.complete(prompt="Analyze this", model="gemini-2.0-flash", timeout_seconds=3)

        self.assertIn("/models/gemini-2.0-flash:generateContent?key=gemini-test-key", captured["url"])
        self.assertEqual(captured["timeout"], 3)
        self.assertEqual(captured["body"]["contents"][0]["parts"][0]["text"], "Analyze this")
        self.assertEqual(captured["body"]["generationConfig"]["response_mime_type"], "application/json")
        self.assertIn('"direction":"neutral"', response)

    async def test_gemini_client_includes_google_error_body_detail(self):
        error_body = b'{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","message":"Quota exceeded."}}'
        http_error = urllib.error.HTTPError(
            url="https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=None,
        )
        http_error.read = lambda: error_body
        client = GeminiGenerateContentClient(api_key="gemini-test-key")

        with patch("urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(DartLlmAnalysisError) as context:
                await client.complete(prompt="{}", model="gemini-2.0-flash", timeout_seconds=1)

        message = str(context.exception)
        self.assertIn("HTTP 429", message)
        self.assertIn("RESOURCE_EXHAUSTED", message)
        self.assertIn("Quota exceeded.", message)
