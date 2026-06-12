"""Panel loading and forward-return preparation.

Forward returns are the only place the future is allowed to appear, and they are
computed here once with an explicit shift so every metric downstream is
structurally lookahead-free: the score at date T may only ever be joined to
``fwd_ret_N`` (close at T+N vs close at T).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ["trade_date", "ticker", "close", "volume"]


def load_panel(path: Path | str) -> pd.DataFrame:
    path = Path(path)
    if path.suffix == ".parquet":
        panel = pd.read_parquet(path)
        panel["ticker"] = panel["ticker"].astype(str)
    else:
        panel = pd.read_csv(path, dtype={"ticker": str})
    missing = [column for column in REQUIRED_COLUMNS if column not in panel.columns]
    if missing:
        raise ValueError(f"panel is missing required columns: {missing}")
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    return panel.sort_values(["ticker", "trade_date"]).reset_index(drop=True)


def add_forward_returns(panel: pd.DataFrame, horizons: tuple[int, ...] = (5, 20)) -> pd.DataFrame:
    """Attach ``fwd_ret_{N}`` per ticker: close[T+N] / close[T] - 1."""
    result = panel.copy()
    grouped = result.groupby("ticker", sort=False)["close"]
    for horizon in horizons:
        result[f"fwd_ret_{horizon}"] = grouped.shift(-horizon) / result["close"] - 1.0
    return result
