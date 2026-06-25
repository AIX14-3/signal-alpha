from app.analyzers.report.valuation import build_valuation_summary


def test_build_valuation_summary_calculates_multiple_distribution_and_gap():
    summary = build_valuation_summary([
        {
            "raw_document_id": 1,
            "broker": "A",
            "publish_date": "2026-06-24",
            "methodology": "PER",
            "applied_multiple": 14.0,
            "implied_multiple": 15.0,
            "peer_group": ["SK Hynix", "Micron"],
            "needs_review": False,
        },
        {
            "raw_document_id": 2,
            "broker": "B",
            "publish_date": "2026-06-23",
            "methodology": "PER",
            "applied_multiple": 21.0,
            "implied_multiple": 20.0,
            "peer_group": ["Micron"],
            "needs_review": False,
        },
        {
            "raw_document_id": 3,
            "broker": "C",
            "publish_date": "2026-06-22",
            "methodology": "DCF",
            "applied_multiple": None,
            "implied_multiple": None,
            "peer_group": [],
            "needs_review": True,
        },
    ])

    assert summary["valuation_count"] == 3
    assert summary["usable_multiple_count"] == 2
    assert summary["implied_multiple_avg"] == 17.5
    assert summary["implied_multiple_median"] == 17.5
    assert summary["implied_multiple_variance"] == 6.25
    assert summary["peer_gap_avg"] == 0.0
    assert summary["needs_review_ratio"] == 0.3333
    assert summary["data_status"] == "ok"
    assert summary["scenario_band"] == {
        "low_multiple": 15.0,
        "base_multiple": 17.5,
        "high_multiple": 20.0,
        "dispersion_level": "medium",
        "confidence_note": "multiple_dispersion_medium",
        "needs_review": False,
    }
    assert summary["methodology_mix"] == [
        {"methodology": "DCF", "count": 1},
        {"methodology": "PER", "count": 2},
    ]
    assert summary["peer_group"] == [
        {"name": "Micron", "count": 2},
        {"name": "SK Hynix", "count": 1},
    ]


def test_build_valuation_summary_marks_partial_when_review_ratio_is_high():
    summary = build_valuation_summary([
        {"raw_document_id": 1, "implied_multiple": 15.0, "needs_review": True},
        {"raw_document_id": 2, "implied_multiple": 16.0, "needs_review": True},
        {"raw_document_id": 3, "implied_multiple": 17.0, "needs_review": False},
    ])

    assert summary["data_status"] == "partial"
    assert summary["needs_review"] is True
    assert "valuation_review_required" in summary["risk_flags"]
    assert summary["scenario_band"]["needs_review"] is True


def test_build_valuation_summary_is_partial_without_rows():
    summary = build_valuation_summary([])

    assert summary["valuation_count"] == 0
    assert summary["usable_multiple_count"] == 0
    assert summary["data_status"] == "partial"
    assert summary["needs_review"] is True
    assert summary["scenario_band"] == {
        "low_multiple": None,
        "base_multiple": None,
        "high_multiple": None,
        "dispersion_level": "unknown",
        "confidence_note": "valuation_data_required",
        "needs_review": True,
    }
