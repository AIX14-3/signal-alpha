"""Realized-volatility utilities — single source of truth for the target.

All six benchmark models forecast the SAME quantity so their outputs are
comparable: the realized volatility over the next ``H`` trading days made at
forecast origin ``asof`` (using only data through ``asof``):

    actual_rv_H(asof) = sqrt( sum_{i=1..H} ret[asof+i]^2 )

where ``ret`` is the close-to-close log return.

Two volatility series appear in the pipeline; keep them straight:
  * ``ret``  — close-to-close log return (drives the scoring target).
  * ``rv_d`` — daily *variance* proxy used as model INPUT. By default the
    Garman-Klass range estimator, which is far less noisy than ret**2 and so
    gives HAR/Chronos/LightGBM a usable signal. Configurable via estimator.

Variances are additive over time, so an H-day forecast is built by summing H
daily variances and taking the square root. Keep everything in *variance*
space until the final sqrt to avoid unit mistakes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def log_returns(close: pd.Series) -> pd.Series:
    """Close-to-close log returns (first value NaN)."""
    return np.log(close / close.shift(1))


def garman_klass_var(df: pd.DataFrame) -> pd.Series:
    """Daily variance estimate from OHLC (Garman-Klass, 1980).

    Uses only same-day O/H/L/C, so it is point-in-time safe.
    """
    o, h, low, c = df["open"], df["high"], df["low"], df["close"]
    hl = np.log(h / low) ** 2
    co = np.log(c / o) ** 2
    return 0.5 * hl - (2.0 * np.log(2.0) - 1.0) * co


def parkinson_var(df: pd.DataFrame) -> pd.Series:
    """Daily variance estimate from the high-low range (Parkinson, 1980)."""
    hl = np.log(df["high"] / df["low"]) ** 2
    return hl / (4.0 * np.log(2.0))


def close_to_close_var(df: pd.DataFrame) -> pd.Series:
    """Squared close-to-close log return (noisy; transparent)."""
    return log_returns(df["close"]) ** 2


_ESTIMATORS = {
    "gk": garman_klass_var,
    "parkinson": parkinson_var,
    "c2c": close_to_close_var,
}


def daily_variance(df: pd.DataFrame, estimator: str = "gk") -> pd.Series:
    """Daily variance proxy series used as MODEL INPUT (rv_d)."""
    if estimator not in _ESTIMATORS:
        raise ValueError(f"unknown estimator {estimator!r}; choose {list(_ESTIMATORS)}")
    return _ESTIMATORS[estimator](df).clip(lower=1e-12)


def future_realized_vol(ret: pd.Series, asof_idx: int, horizon: int) -> float | None:
    """Scoring TARGET: realized vol over (asof, asof+H]. None if unavailable.

    ``ret`` must be the full close-to-close return series; ``asof_idx`` is the
    positional index of the forecast origin. Uses only FUTURE returns, so it is
    never passed to a model — only the harness sees it, for scoring.
    """
    future = ret.iloc[asof_idx + 1 : asof_idx + 1 + horizon]
    if len(future) < horizon or future.isna().any():
        return None
    return float(np.sqrt(np.sum(future.to_numpy() ** 2)))


def h_day_vol_from_daily_var(daily_var: float, horizon: int) -> float:
    """Scale a one-step daily variance forecast to an H-day volatility."""
    return float(np.sqrt(max(daily_var, 1e-12) * horizon))


def h_day_vol_from_path(daily_vars: np.ndarray) -> float:
    """Aggregate a path of H daily variance forecasts to an H-day volatility."""
    return float(np.sqrt(np.sum(np.clip(daily_vars, 1e-12, None))))
