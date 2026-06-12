"""수급 팩터 — 외인+기관 20일 누적 순매수를 거래대금으로 정규화.

현재 패널의 수급 컬럼은 100% 결측(pykrx 익명 접근 제약)이라 전부 NaN이
나온다 — 키움 ka10059 백필(Phase 6) 후 자동으로 활성화되는 구조.
"""

from __future__ import annotations

import pandas as pd

WINDOW = 20


def flow_20(panel: pd.DataFrame, fundamentals: pd.DataFrame | None = None) -> pd.Series:
    net = panel["foreign_net"].astype(float).fillna(0) + panel["institution_net"].astype(float).fillna(0)
    # 둘 다 결측인 행은 0이 아니라 NaN이어야 한다 (결측 제외 규칙)
    both_missing = panel["foreign_net"].isna() & panel["institution_net"].isna()
    net = net.where(~both_missing)

    trade_value = (panel["close"].astype(float) * panel["volume"].astype(float)).where(
        panel["close"] > 0
    )
    by_ticker = panel["ticker"]
    net_sum = net.groupby(by_ticker, sort=False).rolling(WINDOW, min_periods=WINDOW).sum()
    value_sum = trade_value.groupby(by_ticker, sort=False).rolling(WINDOW, min_periods=WINDOW).sum()
    ratio = (net_sum / value_sum).reset_index(level=0, drop=True)
    return ratio
