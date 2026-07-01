import unittest
from datetime import date
from types import SimpleNamespace

from app.agents import SourceAgentInput
from app.agents.dart.agent import DartAnalysisAgent
from app.agents.dart.evidence import (
    DartLlmEvidenceExtractor,
    build_dart_evidence_extractor,
)
from app.analyzers.dart.llm import DartLlmAnalysis


def high_impact_event():
    return {
        "id": 501,
        "event_type": "material_event",
        "event_date": date(2026, 6, 15),
        "signal_direction": "neutral",
        "impact_level": "high",
        "title": "Material disclosure",
        "summary": "DART disclosure: material event",
        "evidence_text": "Revenue improved materially.",
        "needs_review": False,
    }


def low_impact_event():
    return {
        "id": 502,
        "event_type": "dart_disclosure",
        "event_date": date(2026, 6, 15),
        "signal_direction": "neutral",
        "impact_level": "low",
        "title": "Minor notice",
        "needs_review": False,
    }


class FakeAnalyzer:
    """DartLlmAnalyzer 인터페이스 흉내 — analyze/model/prompt_version."""

    model = "fake-model"
    prompt_version = "dart-llm-v1"

    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = 0

    async def analyze(self, *, events, rule_result, stock_code):
        self.calls += 1
        if self.fail:
            raise RuntimeError("LLM timeout")
        return DartLlmAnalysis(
            direction="positive",  # 근거 추출기가 채택하지 말아야 하는 verdict
            score=0.7,
            summary="LLM 근거 요약.",
            key_facts=["매출 개선"],
            risk_flags=["관계자거래"],
            needs_review=True,
            confidence=80,
        )


def _rule_result(events):
    from app.analyzers.dart.source_result import build_dart_analysis_result

    return build_dart_analysis_result(events)


class DartEvidenceExtractorTest(unittest.IsolatedAsyncioTestCase):
    async def test_low_impact_skips_llm(self):
        analyzer = FakeAnalyzer()
        extractor = DartLlmEvidenceExtractor(analyzer=analyzer)
        events = [low_impact_event()]

        result = await extractor.extract(events, stock_code="005930", rule_result=_rule_result(events))

        self.assertIsNone(result.payload)
        self.assertEqual(analyzer.calls, 0)  # 게이트: 고임팩트 아니면 LLM 미호출

    async def test_high_impact_extracts_evidence_without_verdict(self):
        analyzer = FakeAnalyzer()
        extractor = DartLlmEvidenceExtractor(analyzer=analyzer)
        events = [high_impact_event()]

        result = await extractor.extract(events, stock_code="005930", rule_result=_rule_result(events))

        self.assertEqual(analyzer.calls, 1)
        self.assertIsNotNone(result.payload)
        self.assertEqual(result.payload["summary"], "LLM 근거 요약.")
        self.assertEqual(result.payload["key_facts"], ["매출 개선"])
        self.assertEqual(result.payload["llm_model"], "fake-model")
        # verdict(direction/score)는 근거 payload 에 담지 않는다.
        self.assertNotIn("direction", result.payload)
        self.assertNotIn("score", result.payload)
        self.assertEqual(result.llm_model, "fake-model")
        self.assertTrue(result.needs_review)
        self.assertIsNone(result.llm_error)

    async def test_llm_failure_falls_back_to_features(self):
        analyzer = FakeAnalyzer(fail=True)
        extractor = DartLlmEvidenceExtractor(analyzer=analyzer)
        events = [high_impact_event()]

        result = await extractor.extract(events, stock_code="005930", rule_result=_rule_result(events))

        self.assertIsNone(result.payload)  # 근거 블록 생략(결정론 폴백)
        self.assertIsNotNone(result.llm_error)

    def test_build_extractor_disabled_when_flag_off(self):
        settings = SimpleNamespace(dart_use_llm=False, dart_llm_model="gemini-x")
        self.assertIsNone(build_dart_evidence_extractor(settings))

    def test_build_extractor_disabled_when_model_empty(self):
        settings = SimpleNamespace(dart_use_llm=True, dart_llm_model="")
        self.assertIsNone(build_dart_evidence_extractor(settings))


class DartAgentEvidenceIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_agent_attaches_evidence_additively_without_touching_numbers(self):
        extractor = DartLlmEvidenceExtractor(analyzer=FakeAnalyzer())
        agent = DartAnalysisAgent(evidence_extractor=extractor)

        out = await agent.analyze(
            SourceAgentInput(source="DART", stock_code="005930", events=[high_impact_event()])
        )

        # 숫자·계약은 불변.
        self.assertEqual(out.direction, "unknown")
        self.assertEqual(out.score, 0.0)
        self.assertEqual(out.data_status, "no_signal")
        self.assertEqual(out.analysis_source, "features")
        self.assertEqual(out.prompt_ver, "dart-features-v1")
        # 근거는 additive — 기존 피처 키 + llm_evidence.
        self.assertIn("llm_evidence", out.method_detail)
        self.assertEqual(out.method_detail["llm_evidence"]["summary"], "LLM 근거 요약.")
        self.assertEqual(out.method_detail["source"], "DART")
        self.assertIn("derived_features", out.method_detail)
        # LLM 근거 기여 provenance.
        self.assertEqual(out.llm_model, "fake-model")

    async def test_agent_without_extractor_stays_features_only(self):
        agent = DartAnalysisAgent()

        out = await agent.analyze(
            SourceAgentInput(source="DART", stock_code="005930", events=[high_impact_event()])
        )

        self.assertNotIn("llm_evidence", out.method_detail)
        self.assertIsNone(out.llm_model)
        self.assertIsNone(out.llm_error)


if __name__ == "__main__":
    unittest.main()
