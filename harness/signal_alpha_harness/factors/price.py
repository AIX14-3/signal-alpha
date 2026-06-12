"""가격계 팩터 3종 — 거래일 시프트는 종목별 groupby로만 (캘린더 누수 방지).

거래정지 행(close<=0)은 NaN 처리해 수익률 계산이 오염되지 않게 한다.
모든 시프트는 양수(과거 방향)만 사용 — T일 값은 T일까지의 정보로만 계산된다.
"""

from __future__ import annotations

import pandas as pd

MONTH = 21  # 영업일 기준 1개월
YEAR = 252


def _clean_close(panel: pd.DataFrame) -> pd.Series:
    close = panel["close"].astype(float)
    return close.where(close > 0)


def momentum_12_1(panel: pd.DataFrame, fundamentals: pd.DataFrame | None = None) -> pd.Series:
    """12-1 모멘텀: 최근 1개월을 제외한 직전 11개월 수익률.

    close(T-21) / close(T-252) - 1 — 최근 1개월은 단기반전 효과와 상쇄되므로
    제외하는 것이 표준 정의.
    """
    close = _clean_close(panel)
    grouped = close.groupby(panel["ticker"], sort=False)
    return grouped.shift(MONTH) / grouped.shift(YEAR) - 1.0


def reversal_1m(panel: pd.DataFrame, fundamentals: pd.DataFrame | None = None) -> pd.Series:
    """단기반전: 최근 1개월 수익률의 부호 반전 (많이 빠진 종목이 유리)."""
    close = _clean_close(panel)
    grouped = close.groupby(panel["ticker"], sort=False)
    return -(close / grouped.shift(MONTH) - 1.0)


def lowvol_60(panel: pd.DataFrame, fundamentals: pd.DataFrame | None = None) -> pd.Series:
    """저변동성: 60일 일간수익률 표준편차의 부호 반전 (변동성 낮을수록 유리)."""
    close = _clean_close(panel)
    returns = close.groupby(panel["ticker"], sort=False).pct_change()
    vol = (
        returns.groupby(panel["ticker"], sort=False)
        .rolling(60, min_periods=40)
        .std()
        .reset_index(level=0, drop=True)
    )
    return -vol
