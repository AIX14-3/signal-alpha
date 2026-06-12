"""Baseline score used to validate the harness plumbing (Phase 0 only).

A light, vectorized stand-in for the PRICE analyzer (`agent-worker
analyzers/price/rules.py`): 20-day momentum + investor-flow streaks, clamped to
[-1, +1]. It exists so metrics, splits, and the permutation gate can be
exercised end-to-end before the real per-source analyzers are wired in
(Phase 2+). Tuning loops must not treat this as the production scorer.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MOMENTUM_WINDOW = 20
MOMENTUM_WEIGHT = 0.5
FLOW_STREAK_SESSIONS = 3
FOREIGN_WEIGHT = 0.15
INSTITUTION_WEIGHT = 0.1


def _streak_signal(net: pd.Series, sessions: int) -> pd.Series:
    """+1 when the last `sessions` days were all net buying, -1 when all selling."""
    sign = np.sign(net.fillna(0.0))
    all_buying = sign.rolling(sessions).min() > 0
    all_selling = sign.rolling(sessions).max() < 0
    return all_buying.astype(float) - all_selling.astype(float)


def add_baseline_score(panel: pd.DataFrame) -> pd.DataFrame:
    """Attach a `score` column; rows without enough history get NaN."""
    result = panel.sort_values(["ticker", "trade_date"]).reset_index(drop=True)
    by_ticker = result.groupby("ticker", sort=False)

    momentum = by_ticker["close"].transform(lambda s: s / s.shift(MOMENTUM_WINDOW) - 1.0)
    score = np.tanh(momentum * 5.0) * MOMENTUM_WEIGHT
    if "foreign_net" in result:
        score = score + (
            by_ticker["foreign_net"].transform(_streak_signal, FLOW_STREAK_SESSIONS)
            * FOREIGN_WEIGHT
        )
    if "institution_net" in result:
        score = score + (
            by_ticker["institution_net"].transform(_streak_signal, FLOW_STREAK_SESSIONS)
            * INSTITUTION_WEIGHT
        )
    result["score"] = score.clip(-1.0, 1.0)
    return result
