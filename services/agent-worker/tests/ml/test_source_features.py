"""소스 피처 어셈블리 (#525 Phase 1) — PIT(look-ahead 0) + 평탄화 검증.

순수(DB 비접촉) 테스트: 행 dict 를 직접 먹여 known_at<=asof 게이트와 숫자 평탄화를 확인한다.
"""

from __future__ import annotations

from datetime import date

from app.ml.source_features import assemble_features, pit_rows


def _dl(observed: str, index: float, *, spike: bool = False, change: float | None = None) -> dict:
    return {
        "observed_date": observed,
        "search_index": index,
        "is_spike": spike,
        "change_pct": change,
        "weight": 1.0,
        "polarity": "demand",
    }


def test_pit_rows_excludes_future_and_undated():
    asof = date(2026, 6, 1)
    rows = [
        {"observed_date": "2026-05-30"},  # 과거 — 유지
        {"observed_date": "2026-06-01"},  # 당일 — 유지(<=asof)
        {"observed_date": "2026-06-02"},  # 미래 — 제외(look-ahead)
        {"observed_date": None},  # 시점 불명 — 제외(보수적)
        {"observed_date": "not-a-date"},  # 파싱 불가 — 제외
    ]

    kept = pit_rows(rows, asof, date_key="observed_date")

    assert [r["observed_date"] for r in kept] == ["2026-05-30", "2026-06-01"]


def test_assemble_is_look_ahead_safe():
    # asof 이후 행이 섞여도 결과는 asof 이전만 쓴 것과 동일해야 한다(D3, leakage 0).
    asof = date(2026, 6, 1)
    past = [_dl("2026-05-20", 100.0), _dl("2026-05-31", 140.0, spike=True)]
    future = [_dl("2026-06-05", 999.0, spike=True)]

    with_future = assemble_features(asof, datalab_rows=past + future)
    only_past = assemble_features(asof, datalab_rows=past)

    assert with_future["datalab"] == only_past["datalab"]


def test_assemble_keeps_numeric_features_only():
    asof = date(2026, 6, 1)
    features = assemble_features(
        asof,
        datalab_rows=[_dl("2026-05-31", 120.0, spike=True, change=0.1)],
        report_facts=[
            {"publish_date": "2026-05-15", "implied_multiple": 10.0, "applied_multiple": 8.0},
        ],
    )

    # datalab: 숫자 피처만, 결측은 None 으로 유지.
    assert "spike_ratio" in features["datalab"]
    assert all(
        v is None or isinstance(v, float) for v in features["datalab"].values()
    )
    # report: 문자열/리스트 키(methodology_mix, latest_facts, risk_flags, data_status)는 제외.
    for dropped in ("methodology_mix", "latest_facts", "risk_flags", "data_status", "scenario_band"):
        assert dropped not in features["report"]
    assert features["report"]["valuation_count"] == 1.0


def test_report_publish_date_pit():
    asof = date(2026, 6, 1)
    facts = [
        {"publish_date": "2026-05-15", "implied_multiple": 10.0, "applied_multiple": 8.0},
        {"publish_date": "2026-06-10", "implied_multiple": 50.0, "applied_multiple": 8.0},  # 미래
    ]

    features = assemble_features(asof, report_facts=facts)

    # 미래 리포트는 제외 → usable_multiple_count == 1.
    assert features["report"]["valuation_count"] == 1.0
    assert features["report"]["usable_multiple_count"] == 1.0
