from app.collectors.report.parsers import run_parser
from app.collectors.report.parsers.llm_parser import parse_report


class FakeStorage:
    def download_pdf(self, key):
        return b"%PDF-fake"


class Settings:
    report_use_llm = False


def test_process_from_s3_uses_full_text_without_llm_when_disabled(monkeypatch):
    calls = {"llm": 0}

    def fake_extract_text(pdf_bytes, **_kwargs):
        assert pdf_bytes == b"%PDF-fake"
        return (
            "표지\n"
            "요약\n"
            "앞부분에는 정량 정보가 없습니다.\n"
            + ("본문\n" * 200)
            + "투자의견 Buy(Maintain)\n"
            + "목표주가(12M) 480,000원\n"
            + "실적 전망은 메모리 업황 회복과 서버 수요 개선을 근거로 상향 조정되었습니다.\n"
        )

    def fail_if_llm_called(text):
        calls["llm"] += 1
        raise AssertionError("LLM parser should not run when REPORT_USE_LLM=false")

    monkeypatch.setattr(run_parser, "extract_text", fake_extract_text)
    monkeypatch.setattr(run_parser, "parse_report", fail_if_llm_called)

    result = run_parser.process_from_s3("reports/005930/report.pdf", FakeStorage())

    assert calls["llm"] == 0
    assert result["target_price"] == 480000
    assert result["opinion"] == "buy"
    assert "메모리 업황 회복" in result["key_rationale"]
    assert "본문" in result["raw_text"]


def test_process_from_s3_merges_llm_result_only_when_enabled(monkeypatch):
    class LlmSettings:
        report_use_llm = True

    monkeypatch.setattr(
        run_parser,
        "extract_text",
        lambda _: "목표주가 90,000원\n투자의견 중립\n실적 개선 근거가 확인됩니다.",
        raising=False,
    )
    monkeypatch.setattr(
        run_parser,
        "parse_report",
        lambda text, **_kwargs: {
            "target_price": 91000,
            "opinion": "neutral",
            "key_rationale": "LLM 보강 근거",
        },
    )
    monkeypatch.setattr(run_parser, "_build_llm_config", lambda settings: object())

    result = run_parser.process_from_s3(
        "reports/005930/report.pdf",
        FakeStorage(),
        settings=LlmSettings(),
    )

    assert result["target_price"] == 91000
    assert result["opinion"] == "neutral"
    assert result["key_rationale"] == "LLM 보강 근거"


def test_process_from_s3_keeps_deterministic_result_when_llm_fails(monkeypatch):
    class LlmSettings:
        report_use_llm = True

    monkeypatch.setattr(
        run_parser,
        "extract_text",
        lambda _: "목표주가 90,000원\n투자의견 중립\n실적 개선 근거가 확인됩니다.",
    )

    def raise_llm_error(text):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(run_parser, "parse_report", raise_llm_error)

    result = run_parser.process_from_s3(
        "reports/005930/report.pdf",
        FakeStorage(),
        settings=LlmSettings(),
    )

    assert result["target_price"] == 90000
    assert result["opinion"] == "neutral"
    assert "실적 개선 근거" in result["key_rationale"]


class FakeReportLlmConfig:
    model = "gemini-test-model"
    timeout_seconds = 8.0

    def __init__(self):
        self.client = FakeLlmClient()


class FakeLlmClient:
    def __init__(self):
        self.calls = []

    async def complete(self, *, prompt, model, timeout_seconds):
        self.calls.append({
            "prompt": prompt,
            "model": model,
            "timeout_seconds": timeout_seconds,
        })
        return (
            '{"target_price": 123000, "opinion": "buy", '
            '"key_rationale": "Gemini provider result"}'
        )


def test_parse_report_uses_report_llm_config_provider_client():
    config = FakeReportLlmConfig()

    result = parse_report("목표주가 120,000원\n투자의견 Buy", llm_config=config)

    assert result["target_price"] == 123000
    assert result["opinion"] == "buy"
    assert result["key_rationale"] == "Gemini provider result"
    assert config.client.calls[0]["model"] == "gemini-test-model"
    assert config.client.calls[0]["timeout_seconds"] == 8.0


def test_process_from_s3_builds_llm_config_from_settings(monkeypatch):
    class LlmSettings:
        report_use_llm = True
        report_llm_provider = "gemini"
        report_llm_model = "gemini-test-model"

    observed = {}

    monkeypatch.setattr(
        run_parser,
        "extract_text",
        lambda _: "목표주가 90,000원\n투자의견 중립\n실적 개선 근거가 확인됩니다.",
    )

    def fake_build_llm_config(settings):
        observed["settings"] = settings
        return FakeReportLlmConfig()

    def fake_parse_report(text, **kwargs):
        observed["llm_config"] = kwargs["llm_config"]
        return {
            "target_price": 125000,
            "opinion": "buy",
            "key_rationale": "Gemini 보강 근거",
        }

    monkeypatch.setattr(run_parser, "_build_llm_config", fake_build_llm_config)
    monkeypatch.setattr(run_parser, "parse_report", fake_parse_report)

    result = run_parser.process_from_s3(
        "reports/005930/report.pdf",
        FakeStorage(),
        settings=LlmSettings(),
    )

    assert observed["settings"].report_llm_provider == "gemini"
    assert observed["llm_config"].model == "gemini-test-model"
    assert result["target_price"] == 125000
    assert result["opinion"] == "buy"
