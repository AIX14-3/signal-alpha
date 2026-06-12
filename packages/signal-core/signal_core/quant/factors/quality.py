"""퀄리티 팩터 — 영업이익률 수준과 YoY 개선 (DART PIT 재무).

point-in-time 규율: 어떤 보고서의 수치도 ``available_date``(공시 접수일)
이전의 trade_date에는 절대 결합되지 않는다 — ``pd.merge_asof`` backward.

주의: thstrm_amount는 보고서 누적 기준(Q1=3개월, H1=6개월, FY=12개월)이라
기간 유형이 섞이면 마진의 절대 수준이 다르다. 한국 상장사의 공시 캘린더는
거의 동기화되어 있어 같은 날짜의 횡단면은 대부분 같은 기간 유형을 보지만,
YoY 개선은 **같은 period_type끼리만** 비교해 이 문제를 원천 차단한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _prepare(fundamentals: pd.DataFrame) -> pd.DataFrame:
    fund = fundamentals.dropna(subset=["available_date"]).copy()
    fund["available_date"] = pd.to_datetime(fund["available_date"])
    fund["margin"] = fund["operating_income"].astype(float) / fund["revenue"].astype(float).where(
        fund["revenue"].astype(float) > 0
    )
    # 같은 period_type의 전년 마진과 비교 (Q1↔Q1, FY↔FY)
    fund = fund.sort_values(["ticker", "period_type", "bsns_year"])
    prev = fund.groupby(["ticker", "period_type"], sort=False)["margin"].shift(1)
    prev_year = fund.groupby(["ticker", "period_type"], sort=False)["bsns_year"].shift(1)
    fund["margin_yoy"] = (fund["margin"] - prev).where(
        fund["bsns_year"].astype(float) - prev_year.astype(float) == 1.0
    )
    return fund


def _asof_join(panel: pd.DataFrame, fund: pd.DataFrame, column: str) -> pd.Series:
    """종목별 merge_asof: trade_date 시점에 '이미 공시된' 최신 값만 붙인다."""
    left = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(panel["trade_date"]),
            "ticker": panel["ticker"].astype(str),
        },
        index=panel.index,
    )
    right = (
        fund.dropna(subset=[column])[["ticker", "available_date", column]]
        .rename(columns={"available_date": "asof_date"})
        .sort_values("asof_date")
    )
    right["ticker"] = right["ticker"].astype(str)

    ordered = left.sort_values("trade_date").reset_index()  # merge_asof는 정렬 필수
    joined = pd.merge_asof(
        ordered,
        right,
        left_on="trade_date",
        right_on="asof_date",
        by="ticker",
        direction="backward",
    )
    return joined.set_index("index")[column].reindex(panel.index)


def asof_fundamental_column(
    panel: pd.DataFrame, fundamentals: pd.DataFrame, column: str
) -> pd.Series:
    """PIT as-of 결합 헬퍼 — value 팩터 등 다른 모듈도 재사용."""
    fund = fundamentals.dropna(subset=["available_date"]).copy()
    fund["available_date"] = pd.to_datetime(fund["available_date"])
    return _asof_join(panel, fund, column)


def quality_margin(panel: pd.DataFrame, fundamentals: pd.DataFrame | None = None) -> pd.Series:
    if fundamentals is None:
        return pd.Series(np.nan, index=panel.index, dtype="float64")
    return _asof_join(panel, _prepare(fundamentals), "margin")


def quality_margin_yoy(panel: pd.DataFrame, fundamentals: pd.DataFrame | None = None) -> pd.Series:
    if fundamentals is None:
        return pd.Series(np.nan, index=panel.index, dtype="float64")
    return _asof_join(panel, _prepare(fundamentals), "margin_yoy")
