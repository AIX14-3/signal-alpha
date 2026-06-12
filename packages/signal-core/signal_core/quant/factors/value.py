"""밸류 팩터 — BPR(자본총계/시가총액). 시가총액 데이터 확보 전까지 보류.

pykrx의 시총·PER/PBR 엔드포인트(전종목·기간 조회 모두)는 2026-06 현재 익명
접근이 차단되어 빈 응답을 준다 (개별 일봉만 정상). 후속 수집 계획:
DART 주식총수현황(stockTotqySttus, rcept 기반 PIT) × 비수정 종가
(``get_market_ohlcv(..., adjusted=False)``)로 시총을 직접 구성한다 —
수정주가 × 현재 주식수 조합은 분할 전후 시총이 왜곡되므로 금지.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def value_bpr(panel: pd.DataFrame, fundamentals: pd.DataFrame | None = None) -> pd.Series:
    if "market_cap" not in panel.columns or fundamentals is None:
        return pd.Series(np.nan, index=panel.index, dtype="float64")

    from signal_core.quant.factors.quality import asof_fundamental_column

    equity = asof_fundamental_column(panel, fundamentals, "total_equity")
    cap = panel["market_cap"].astype(float).where(panel["market_cap"] > 0)
    return equity / cap
