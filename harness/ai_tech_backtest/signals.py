"""Algorithm 'refinement' step: indicators -> directional signal.

Two predictors share one interface (a function mapping a feature DataFrame to
an array of {-1,+1} direction predictions):

1. rule_predict  - transparent, parameter-light combination of the indicators
                   the user named (MACD, Stochastic, StochRSI, OBV, RSI).
                   No fitting, so it cannot overfit the test window.
2. MLModel       - optional logistic regression, fit ONLY on the train window
                   inside each walk-forward fold (see backtest.py).

Keeping the rule model dead simple is intentional: on noisy daily data an
over-tuned model mostly memorises noise.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import FEATURE_COLS


def rule_score(df: pd.DataFrame) -> pd.Series:
    """Combine indicators into a [-1, +1] directional score (vectorised)."""
    s = pd.Series(0.0, index=df.index)

    # Trend / momentum: MACD histogram sign.
    s += np.sign(df["macd_hist"].fillna(0)) * 0.30
    # RSI regime: above 50 bullish, below 50 bearish (scaled, clipped).
    s += ((df["rsi14"] - 50) / 50).clip(-1, 1).fillna(0) * 0.20
    # Stochastic momentum: %K vs %D.
    s += np.sign((df["stoch_k"] - df["stoch_d"]).fillna(0)) * 0.15
    # Stochastic RSI momentum.
    s += np.sign((df["srsi_k"] - df["srsi_d"]).fillna(0)) * 0.15
    # Volume confirmation: OBV slope direction.
    s += np.sign(df["obv_slope"].fillna(0)) * 0.20

    return s.clip(-1, 1)


def rule_predict(df: pd.DataFrame) -> np.ndarray:
    """Direction prediction {-1,+1} from the rule score (0 -> +1 tie-break)."""
    score = rule_score(df).to_numpy()
    return np.where(score >= 0, 1, -1)


class MLModel:
    """Thin logistic-regression wrapper. sklearn is optional."""

    def __init__(self) -> None:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import make_pipeline

        self.pipe = make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1000, C=0.5),
        )
        self.fitted = False

    def fit(self, train: pd.DataFrame, label_col: str) -> "MLModel":
        d = train.dropna(subset=FEATURE_COLS + [label_col])
        d = d[d[label_col] != 0]  # binary up/down only
        if d[label_col].nunique() < 2 or len(d) < 50:
            return self  # not enough signal to fit; stays unfitted
        self.pipe.fit(d[FEATURE_COLS], (d[label_col] > 0).astype(int))
        self.fitted = True
        return self

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        x = test[FEATURE_COLS].fillna(0)
        proba = self.pipe.predict_proba(x)[:, 1]
        return np.where(proba >= 0.5, 1, -1)
