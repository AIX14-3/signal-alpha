from app.collectors.report.parsers.valuation_llm import enrich_valuation_facts_with_llm


class FakeReportLlmConfig:
    model = "gemini-test-model"
    timeout_seconds = 7.0

    def __init__(self, response: str):
        self.client = FakeLlmClient(response)


class FakeLlmClient:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    async def complete(self, *, prompt, model, timeout_seconds):
        self.calls.append({
            "prompt": prompt,
            "model": model,
            "timeout_seconds": timeout_seconds,
        })
        return self.response


def _base_facts():
    return {
        "target_price": 120000,
        "forward_eps_est": 8000,
        "eps_fy": 2026,
        "methodology": "PER",
        "applied_multiple": 15.0,
        "implied_multiple": 15.0,
        "peer_group": ["SK Hynix", "Micron"],
        "category_tag": None,
        "rerating_thesis": None,
        "extraction_source": "rules",
        "needs_review": False,
    }


def test_enrich_valuation_facts_ignores_llm_numeric_fields_and_merges_safe_fields():
    config = FakeReportLlmConfig(
        """
        {
          "target_price": 1,
          "forward_eps_est": 1,
          "implied_multiple": 999,
          "methodology": "DCF",
          "category_tag": "ai_memory",
          "rerating_thesis": "HBM demand and peer premium explain the valuation context.",
          "needs_review": false
        }
        """
    )

    result = enrich_valuation_facts_with_llm(
        "Target Price KRW 120,000\n2026E EPS 8,000",
        _base_facts(),
        llm_config=config,
    )

    assert result["target_price"] == 120000
    assert result["forward_eps_est"] == 8000
    assert result["implied_multiple"] == 15.0
    assert result["methodology"] == "DCF"
    assert result["category_tag"] == "ai_memory"
    assert result["rerating_thesis"] == "HBM demand and peer premium explain the valuation context."
    assert result["extraction_source"] == "llm"
    assert result["needs_review"] is False
    assert config.client.calls[0]["model"] == "gemini-test-model"
    assert "Do not create or change numeric values" in config.client.calls[0]["prompt"]


def test_enrich_valuation_facts_falls_back_when_llm_output_contains_advice():
    config = FakeReportLlmConfig(
        """
        {
          "methodology": "PER",
          "category_tag": "ai_memory",
          "rerating_thesis": "Buy now because the target price implies upside.",
          "needs_review": false
        }
        """
    )

    result = enrich_valuation_facts_with_llm(
        "Target Price KRW 120,000\n2026E EPS 8,000",
        _base_facts(),
        llm_config=config,
    )

    assert result["target_price"] == 120000
    assert result["methodology"] == "PER"
    assert result["category_tag"] is None
    assert result["rerating_thesis"] is None
    assert result["extraction_source"] == "rules_fallback"
    assert result["needs_review"] is True


def test_enrich_valuation_facts_falls_back_on_invalid_json():
    config = FakeReportLlmConfig("not json")

    result = enrich_valuation_facts_with_llm(
        "Target Price KRW 120,000\n2026E EPS 8,000",
        _base_facts(),
        llm_config=config,
    )

    assert result["extraction_source"] == "rules_fallback"
    assert result["needs_review"] is True
