"""Load daily closes from a local CSV into PriceSeries (no DB, no API key).

For the DataLab-only backtest we keep prices OUT of ``ohlcv_data`` (the price
team's table) and read them from a file produced by ``scripts/backfill_prices_fdr.py``
(FinanceDataReader). This keeps the alternative-data experiment self-contained.

Expected CSV columns (header required): ``ticker,date,close``. One row per
trading day per ticker; the benchmark (e.g. KOSPI ``KS11``) is just another
ticker. Rows with a blank/non-numeric close are skipped.
"""

from __future__ import annotations

import csv
from datetime import date

from .datalab_dataset import PriceSeries


def load_prices_csv(path: str) -> dict[str, PriceSeries]:
    """Return ``{ticker: PriceSeries}`` from a ``ticker,date,close`` CSV."""
    pairs: dict[str, list[tuple[date, float]]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        _require_columns(reader.fieldnames, path)
        for row in reader:
            close = _to_float(row.get("close"))
            d = _to_date(row.get("date"))
            ticker = (row.get("ticker") or "").strip()
            if close is None or d is None or not ticker:
                continue
            pairs.setdefault(ticker, []).append((d, close))
    return {ticker: PriceSeries.from_pairs(ps) for ticker, ps in pairs.items()}


def _require_columns(fieldnames, path: str) -> None:
    needed = {"ticker", "date", "close"}
    have = {(f or "").strip() for f in (fieldnames or [])}
    missing = needed - have
    if missing:
        raise ValueError(f"{path} is missing columns {sorted(missing)}; got {sorted(have)}")


def _to_float(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _to_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
