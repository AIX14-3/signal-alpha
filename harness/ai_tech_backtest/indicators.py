"""Technical indicators (pure pandas/numpy, all causal — no look-ahead).

Implements the indicators the user asked for: candlestick features,
Stochastic, Stochastic RSI, OBV, MACD (plus RSI which StochRSI needs).
Mirrors the "pure math, minimal deps" style of
services/agent-worker/app/analyzers/price/indicators.py, but vectorised.

Every column is computed from current-and-past rows only, so when row t is
used as a feature it never sees t+1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder smoothing via EWM (adjust=False).
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    line = ema_fast - ema_slow
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def stochastic(high, low, close, k: int = 14, d: int = 3):
    lowest = low.rolling(k).min()
    highest = high.rolling(k).max()
    rng = (highest - lowest).replace(0, np.nan)
    pct_k = 100 * (close - lowest) / rng
    pct_d = pct_k.rolling(d).mean()
    return pct_k, pct_d


def stoch_rsi(close: pd.Series, period: int = 14, k: int = 3, d: int = 3):
    r = rsi(close, period)
    lo = r.rolling(period).min()
    hi = r.rolling(period).max()
    rng = (hi - lo).replace(0, np.nan)
    raw = 100 * (r - lo) / rng
    pct_k = raw.rolling(k).mean()
    pct_d = pct_k.rolling(d).mean()
    return pct_k, pct_d


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume.fillna(0)).cumsum()


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with all indicator + candlestick columns added."""
    out = df.copy()
    c, h, l, v, o = out["close"], out["high"], out["low"], out["volume"], out["open"]

    out["rsi14"] = rsi(c)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(c)
    out["stoch_k"], out["stoch_d"] = stochastic(h, l, c)
    out["srsi_k"], out["srsi_d"] = stoch_rsi(c)
    obv_series = obv(c, v)
    # OBV slope (normalised) is more comparable across stocks than raw level.
    out["obv"] = obv_series
    out["obv_slope"] = obv_series.diff(5) / v.rolling(20).mean().replace(0, np.nan)

    # Candlestick geometry.
    rng = (h - l).replace(0, np.nan)
    out["body_ratio"] = (c - o).abs() / rng
    out["upper_wick"] = (h - c.combine(o, max)) / rng
    out["lower_wick"] = (c.combine(o, min) - l) / rng
    out["bullish"] = (c > o).astype(int)
    out["gap"] = o / c.shift(1) - 1

    return out


# Indicator-derived feature columns used downstream by signals/llm.
FEATURE_COLS = [
    "rsi14", "macd", "macd_signal", "macd_hist",
    "stoch_k", "stoch_d", "srsi_k", "srsi_d",
    "obv_slope", "body_ratio", "upper_wick", "lower_wick", "bullish", "gap",
]
