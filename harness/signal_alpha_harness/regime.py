"""시장 국면 라벨 + 국면별 IC 분해 (Phase 4).

국면은 유니버스 등가중 시장 수익률의 **직전 60영업일 누적**으로 정한다 —
T일 라벨은 T일까지의 정보만 사용한다 (point-in-time).

  상승(bull):  60일 누적 > +5%
  하락(bear):  60일 누적 < -5%
  횡보(flat):  그 외

게이트(설계 문서 Phase 4): 하락 국면 IC > 0 — "하락장에서도 순위 정보가
유지되는가"가 일관성의 핵심 증거.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from signal_alpha_harness.metrics import daily_spearman_ic

WINDOW = 60
BULL_THRESHOLD = 0.05
BEAR_THRESHOLD = -0.05


def market_return(panel: pd.DataFrame) -> pd.Series:
    """일별 유니버스 등가중 수익률 (trade_date 인덱스, 정렬)."""
    close = panel["close"].astype(float).where(panel["close"] > 0)
    returns = close.groupby(panel["ticker"], sort=False).pct_change()
    return returns.groupby(panel["trade_date"], sort=False).mean().sort_index()


def label_regimes(panel: pd.DataFrame) -> pd.Series:
    """trade_date 인덱스의 국면 라벨 Series ('bull'|'bear'|'flat', 워밍업은 NaN)."""
    market = market_return(panel)
    cumulative = (1.0 + market).rolling(WINDOW, min_periods=WINDOW).apply(np.prod, raw=True) - 1.0
    labels = pd.Series(pd.NA, index=cumulative.index, dtype="object")
    labels[cumulative > BULL_THRESHOLD] = "bull"
    labels[cumulative < BEAR_THRESHOLD] = "bear"
    labels[(cumulative <= BULL_THRESHOLD) & (cumulative >= BEAR_THRESHOLD)] = "flat"
    return labels


def regime_ic_breakdown(scored: pd.DataFrame, horizon: int = 20) -> pd.DataFrame:
    """국면별 mean IC / IC 양봉 비율 / 일수."""
    labels = label_regimes(scored)
    frame = scored.copy()
    frame["regime"] = frame["trade_date"].map(labels)

    rows = []
    for regime in ("bull", "flat", "bear"):
        sub = frame[frame["regime"] == regime]
        if sub.empty:
            rows.append({"regime": regime, "n_days": 0, "mean_ic": None, "ic_positive_share": None})
            continue
        scores = sub.pivot_table(index="trade_date", columns="ticker", values="score", aggfunc="first")
        returns = sub.pivot_table(
            index="trade_date", columns="ticker", values=f"fwd_ret_{horizon}", aggfunc="first"
        ).reindex(index=scores.index, columns=scores.columns)
        ics = daily_spearman_ic(scores.to_numpy(dtype=float), returns.to_numpy(dtype=float))
        valid = ics[~np.isnan(ics)]
        rows.append(
            {
                "regime": regime,
                "n_days": int(len(valid)),
                "mean_ic": float(valid.mean()) if len(valid) else None,
                "ic_positive_share": float((valid > 0).mean()) if len(valid) else None,
            }
        )
    return pd.DataFrame(rows)
