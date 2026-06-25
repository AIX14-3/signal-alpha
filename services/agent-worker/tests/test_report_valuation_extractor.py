from app.collectors.report.parsers.valuation_extractor import extract_valuation_facts


def test_extract_valuation_facts_calculates_implied_multiple_from_eps():
    text = """
    Samsung Electronics
    Target Price KRW 120,000
    2026E EPS 8,000원
    Target PER 15.0x
    peer group: SK Hynix, Micron
    valuation methodology PER
    """

    result = extract_valuation_facts(
        text,
        target_price=120000,
        broker="Test Securities",
        publish_date="2026-06-24",
    )

    assert result["target_price"] == 120000
    assert result["forward_eps_est"] == 8000
    assert result["eps_fy"] == 2026
    assert result["methodology"] == "PER"
    assert result["applied_multiple"] == 15.0
    assert result["implied_multiple"] == 15.0
    assert result["peer_group"] == ["SK Hynix", "Micron"]
    assert result["extraction_source"] == "rules"
    assert result["needs_review"] is False


def test_extract_valuation_facts_supports_korean_eps_and_multiple_terms():
    text = """
    목표주가 120,000원
    2026E 지배주주 EPS 8,000원
    목표 PER 15.0배
    Peer Group: SK하이닉스, Micron
    """

    result = extract_valuation_facts(
        text,
        target_price=120000,
        broker="Test Securities",
        publish_date="2026-06-24",
    )

    assert result["forward_eps_est"] == 8000
    assert result["eps_fy"] == 2026
    assert result["methodology"] == "PER"
    assert result["applied_multiple"] == 15.0
    assert result["implied_multiple"] == 15.0
    assert result["peer_group"] == ["SK하이닉스", "Micron"]
    assert result["needs_review"] is False


def test_extract_valuation_facts_marks_review_when_eps_missing():
    result = extract_valuation_facts(
        "Target Price KRW 90,000\nvaluation methodology DCF",
        target_price=90000,
        broker="Test Securities",
        publish_date="2026-06-24",
    )

    assert result["target_price"] == 90000
    assert result["forward_eps_est"] is None
    assert result["implied_multiple"] is None
    assert result["methodology"] == "DCF"
    assert result["needs_review"] is True
