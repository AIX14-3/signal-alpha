"""Adapt signal-alpha OHLCV rows → vol-benchmark ``DataContract``.

The vol-benchmark models all read a frozen, point-in-time-safe canonical frame
(``date, ticker, open, high, low, close, volume, ret, rv_d``) through
``DataContract`` so that every model sees byte-identical input. signal-alpha
stores daily candles in ``ohlcv_data``; the ``PRICE`` collector
(``collectors/price/ohlcv_reader.py``) already reads them back as JSON-safe row
dicts (keys: ``trade_date, open, high, low, close, volume, ...``). This module
turns those same row dicts into the canonical frame + DataContract.

``ret`` (log return) and ``rv_d`` (daily variance proxy) are computed with the
vendored ``vol_models.common.rv`` helpers — the SAME code the benchmark uses —
so adapter output is comparable to a standalone benchmark run. Both are
point-in-time safe (same-day or prior-day only), so no look-ahead is introduced.

This module does NOT touch the database: callers pass rows already read by the
collector/repository, keeping the adapter pure and unit-testable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

from vol_models.common.data_contract import DataContract
from vol_models.common.rv import daily_variance, log_returns

# Garman-Klass needs O/H/L/C; matches the benchmark's default ``rv_d`` estimator.
DEFAULT_ESTIMATOR = "gk"

# DataContract requires these source columns to exist before ret/rv_d are derived.
_BASE_COLUMNS = ("open", "high", "low", "close", "volume")


def build_frame(
    rows: Iterable[Mapping[str, Any]],
    *,
    ticker: str,
    estimator: str = DEFAULT_ESTIMATOR,
) -> pd.DataFrame:
    """Build the canonical per-ticker frame from OHLCV row dicts.

    ``rows`` items must carry ``trade_date`` (ISO string or date) and numeric
    ``open/high/low/close/volume`` — exactly what ``OhlcvReader`` emits in
    ``RawEvidence.metadata["rows"]``. Rows are sorted ascending by date; ``ret``
    and ``rv_d`` are derived with the vendored benchmark helpers.
    """
    records = list(rows)
    if not records:
        raise ValueError("build_frame requires at least one OHLCV row")

    frame = pd.DataFrame.from_records(records)
    missing = [c for c in ("trade_date", *_BASE_COLUMNS) if c not in frame.columns]
    if missing:
        raise ValueError(f"OHLCV rows missing required keys: {missing}")

    frame["date"] = pd.to_datetime(frame["trade_date"])
    frame["ticker"] = str(ticker)
    for column in _BASE_COLUMNS:
        frame[column] = pd.to_numeric(frame[column])
    frame = frame.sort_values("date").reset_index(drop=True)

    # Derived, point-in-time-safe columns (same code path as build_dataset.py).
    frame["ret"] = log_returns(frame["close"])
    frame["rv_d"] = daily_variance(frame, estimator=estimator)

    return frame[
        ["date", "ticker", "open", "high", "low", "close", "volume", "ret", "rv_d"]
    ]


def build_contract(
    rows: Iterable[Mapping[str, Any]],
    *,
    ticker: str,
    estimator: str = DEFAULT_ESTIMATOR,
) -> DataContract:
    """Build a point-in-time ``DataContract`` from OHLCV row dicts."""
    return DataContract(build_frame(rows, ticker=ticker, estimator=estimator))
