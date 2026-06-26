"""DART 이벤트 indicators (#525 Phase 1, DART 편) — 순수 집계·카테고라이저 검증."""

from __future__ import annotations

from datetime import date

from app.analyzers.dart.indicators import (
    DartIndicators,
    categorize_reason,
    compute_indicators,
)


def _evt(report_date: str, **kw) -> dict:
    base = {
        "report_date": report_date,
        "holder_type": "major",
        "shares_delta": None,
        "ratio_delta": None,
        "report_reason": None,
    }
    base.update(kw)
    return base


def test_categorize_reason_pledge_loan_takes_priority():
    assert categorize_reason("주식담보제공") == "pledge_loan"
    assert categorize_reason("대차거래") == "pledge_loan"
    assert categorize_reason("차입") == "pledge_loan"


def test_categorize_reason_direction_keywords():
    assert categorize_reason("장내매수") == "bullish"
    assert categorize_reason("신규상장") == "bullish"
    assert categorize_reason("장내매도") == "bearish"
    assert categorize_reason("처분") == "bearish"
    assert categorize_reason("사장") == "other"  # elestock 직위 = 방향 없음
    assert categorize_reason(None) == "other"


def test_empty_rows_return_zero_indicators():
    ind = compute_indicators([], as_of=date(2026, 6, 1), lookback_days=180)
    assert ind == DartIndicators(
        observations=0,
        recent_observations=0,
        major_holder_count=0,
        executive_count=0,
        main_shareholder_count=0,
        net_shares_delta=None,
        net_ratio_delta=None,
        avg_ratio_delta=None,
        accumulation_count=0,
        disposal_count=0,
        accumulation_ratio=None,
        recent_net_shares_delta=None,
        pledge_loan_count=0,
        pledge_loan_flag=0.0,
        bullish_reason_count=0,
        bearish_reason_count=0,
        latest_report_date=None,
        days_since_latest=None,
    )


def test_accumulation_and_holder_counts():
    asof = date(2026, 6, 1)
    rows = [
        _evt("2026-05-20", holder_type="major", shares_delta=1000, ratio_delta=0.5),
        _evt("2026-05-25", holder_type="executive", shares_delta=-300, ratio_delta=-0.2),
        _evt("2026-05-28", holder_type="main_shareholder", shares_delta=500, ratio_delta=0.1),
    ]
    ind = compute_indicators(rows, as_of=asof, lookback_days=30)
    assert ind.observations == 3
    assert ind.major_holder_count == 1
    assert ind.executive_count == 1
    assert ind.main_shareholder_count == 1
    assert ind.accumulation_count == 2
    assert ind.disposal_count == 1
    assert ind.net_shares_delta == 1200.0
    assert ind.accumulation_ratio == 2 / 3
    assert ind.latest_report_date == "2026-05-28"
    assert ind.days_since_latest == 4


def test_pledge_loan_flag_set():
    asof = date(2026, 6, 1)
    rows = [_evt("2026-05-20", report_reason="주식담보계약 체결")]
    ind = compute_indicators(rows, as_of=asof, lookback_days=30)
    assert ind.pledge_loan_count == 1
    assert ind.pledge_loan_flag == 1.0
