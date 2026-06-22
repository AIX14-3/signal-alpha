"""DataContract — guarantees every model reads the SAME frozen input.

The benchmark's whole point is "identical input → comparable output". This
module is the single gate through which models obtain their inputs: given one
canonical per-ticker frame and a forecast origin ``asof``, it returns the slice
each model needs, always restricted to rows ``<= asof`` (no look-ahead).

Canonical frame columns (produced by build_dataset.py / the real loader):
    date, ticker, open, high, low, close, volume, ret, rv_d

``ret`` = close-to-close log return, ``rv_d`` = daily variance proxy (see rv.py).
Both are point-in-time safe (computed from the same or prior day only).
"""

from __future__ import annotations

import pandas as pd

REQUIRED_COLUMNS = [
    "date",
    "ticker",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "ret",
    "rv_d",
]


class DataContract:
    """Point-in-time views of one ticker's history."""

    def __init__(self, frame: pd.DataFrame) -> None:
        missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
        if missing:
            raise ValueError(f"frame missing required columns: {missing}")
        # one ticker, sorted ascending by date, integer-positional index
        self.frame = frame.sort_values("date").reset_index(drop=True)

    def __len__(self) -> int:
        return len(self.frame)

    def history(self, asof_idx: int) -> pd.DataFrame:
        """Full OHLCV+derived rows through ``asof`` (inclusive). Models slice
        from this so they all start from byte-identical data."""
        return self.frame.iloc[: asof_idx + 1]

    def returns(self, asof_idx: int) -> pd.Series:
        """Close-to-close return series through asof (NaNs dropped)."""
        return self.frame["ret"].iloc[: asof_idx + 1].dropna()

    def daily_var(self, asof_idx: int) -> pd.Series:
        """Daily variance proxy (rv_d) series through asof."""
        return self.frame["rv_d"].iloc[: asof_idx + 1].dropna()

    def ohlcv(self, asof_idx: int) -> pd.DataFrame:
        """OHLCV candles through asof (Kronos input)."""
        cols = ["open", "high", "low", "close", "volume"]
        return self.frame[cols].iloc[: asof_idx + 1]

    def asof_date(self, asof_idx: int):
        return self.frame["date"].iloc[asof_idx]
