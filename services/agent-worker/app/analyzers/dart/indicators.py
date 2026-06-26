"""Pure aggregation over DART ownership/insider events (#525 Phase 1, DART 편).

``dart_ownership_events`` 행(임원·주요주주 지분변동)을 소스 base 모델/메타러너 입력용 정형 피처로
모은다. 결정론 판정이 아니라 순수 집계 — clock-free, ``as_of`` 는 로더가 공급(D3 PIT 게이트는
``source_features.pit_rows`` 가 ``report_date <= asof`` 로 강제). datalab/hiring indicators 와 동일
관례: 결측은 None, 문자열 필드(latest_report_date)는 후단 ``_numeric`` 평탄화에서 제외된다.

방향성은 ``shares_delta`` 부호(취득/처분)를 주신호로 삼는다 — ``report_reason`` 표현(majorstock=
report_tp, elestock=직위/주요주주 관계)은 이질적이라 보조 플래그(담보/대차/차입=유동성 리스크)로만.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

# report_reason 카테고라이저 — 미지원 표현은 'other' 폴백(plan 위험: ownership_api.py 실값 확인).
# 담보/대차/차입(질권·신탁 포함)은 잠재 매도압력/유동성 리스크 플래그로 별도 집계.
_PLEDGE_LOAN = ("담보", "대차", "차입", "질권", "신탁")
_BULLISH = ("매수", "취득", "신규", "증자", "인수")
_BEARISH = ("매도", "처분", "소각")


def categorize_reason(reason: str | None) -> str:
    """report_reason → {'pledge_loan','bullish','bearish','other'}. 담보/차입 우선(리스크)."""
    if not reason:
        return "other"
    text = str(reason)
    if any(token in text for token in _PLEDGE_LOAN):
        return "pledge_loan"
    if any(token in text for token in _BULLISH):
        return "bullish"
    if any(token in text for token in _BEARISH):
        return "bearish"
    return "other"


@dataclass(frozen=True)
class DartIndicators:
    observations: int
    recent_observations: int  # report_date > midpoint (최근 절반 윈도우)
    major_holder_count: int
    executive_count: int
    main_shareholder_count: int
    # 지분변동(취득/처분) — shares_delta 부호가 주 방향신호.
    net_shares_delta: float | None
    net_ratio_delta: float | None
    avg_ratio_delta: float | None
    accumulation_count: int  # shares_delta > 0
    disposal_count: int  # shares_delta < 0
    accumulation_ratio: float | None  # acc / (acc + disp)
    recent_net_shares_delta: float | None  # 최근 절반 윈도우만
    # report_reason 보조 플래그.
    pledge_loan_count: int
    pledge_loan_flag: float  # 0/1 — 담보/대차/차입 존재
    bullish_reason_count: int
    bearish_reason_count: int
    latest_report_date: str | None
    days_since_latest: int | None


def compute_indicators(
    rows: list[dict],
    *,
    as_of: date,
    lookback_days: int,
) -> DartIndicators:
    observations = len(rows)
    if observations == 0:
        return DartIndicators(
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

    midpoint = as_of - timedelta(days=max(1, lookback_days) // 2)
    recent_observations = 0
    major = executive = main_shareholder = 0
    shares_delta_sum = 0.0
    ratio_delta_sum = 0.0
    ratio_delta_n = 0
    accumulation = disposal = 0
    recent_shares_delta_sum = 0.0
    recent_shares_delta_n = 0
    pledge_loan = bullish_reason = bearish_reason = 0
    latest: date | None = None

    for row in rows:
        observed = _parse_date(row.get("report_date"))
        if observed is not None and (latest is None or observed > latest):
            latest = observed
        is_recent = observed is not None and observed > midpoint
        if is_recent:
            recent_observations += 1

        holder_type = row.get("holder_type")
        if holder_type == "major":
            major += 1
        elif holder_type == "executive":
            executive += 1
        elif holder_type == "main_shareholder":
            main_shareholder += 1

        shares_delta = _as_float(row.get("shares_delta"))
        if shares_delta is not None:
            shares_delta_sum += shares_delta
            if shares_delta > 0:
                accumulation += 1
            elif shares_delta < 0:
                disposal += 1
            if is_recent:
                recent_shares_delta_sum += shares_delta
                recent_shares_delta_n += 1

        ratio_delta = _as_float(row.get("ratio_delta"))
        if ratio_delta is not None:
            ratio_delta_sum += ratio_delta
            ratio_delta_n += 1

        category = categorize_reason(row.get("report_reason"))
        if category == "pledge_loan":
            pledge_loan += 1
        elif category == "bullish":
            bullish_reason += 1
        elif category == "bearish":
            bearish_reason += 1

    directional = accumulation + disposal
    return DartIndicators(
        observations=observations,
        recent_observations=recent_observations,
        major_holder_count=major,
        executive_count=executive,
        main_shareholder_count=main_shareholder,
        net_shares_delta=shares_delta_sum if (accumulation or disposal) else None,
        net_ratio_delta=ratio_delta_sum if ratio_delta_n else None,
        avg_ratio_delta=(ratio_delta_sum / ratio_delta_n) if ratio_delta_n else None,
        accumulation_count=accumulation,
        disposal_count=disposal,
        accumulation_ratio=(accumulation / directional) if directional else None,
        recent_net_shares_delta=recent_shares_delta_sum if recent_shares_delta_n else None,
        pledge_loan_count=pledge_loan,
        pledge_loan_flag=1.0 if pledge_loan else 0.0,
        bullish_reason_count=bullish_reason,
        bearish_reason_count=bearish_reason,
        latest_report_date=latest.isoformat() if latest else None,
        days_since_latest=(as_of - latest).days if latest else None,
    )


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
