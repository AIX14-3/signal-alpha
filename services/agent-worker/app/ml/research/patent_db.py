"""DB adapter: fetch real PATENT + price rows and build a bake-off Dataset.

Mirrors ``datalab_db`` but for patents. Reuses ``RawDetailRepository.
list_patent_details_by_stock`` (the shipping query) and ``MarketDataRepository``
for prices, then hands plain rows to the pure ``patent_dataset.build_dataset``.

Key differences vs datalab_db:
- Each shaped row carries a parsed PUBLICATION date (from ``extra_payload``); both
  the window field (``publication_date``) and the momentum field
  (``application_date``, which ``compute_indicators`` reads) are set to it, so the
  whole feature is look-ahead safe (see ``patent_dataset`` docstring).
- Patents are loaded with ``since_date=None`` (full history per stock): a filing's
  publication lags it ~18 months, so publications landing in the price window come
  from filings well before the window — a date-bounded fetch would miss them.

Run via:
    DATABASE_URL=... python -m app.ml.research.bakeoff --source patent-db \
        --tickers 005930,000660,035420 --start 2021-01-01 --end 2023-12-31 \
        --prices-csv prices_2021_2023.csv --benchmark KS11
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from .datalab_dataset import Dataset, PriceSeries, build_dataset as _datalab_build  # noqa: F401 (PriceSeries reuse)
from .datalab_dataset import _as_date, weekly_signal_dates
from .datalab_db import resolve_stock_ids
from .patent_dataset import build_dataset


def _parse_yyyymmdd(value: Any) -> date | None:
    """Parse a publication/filing date stored as ``YYYYMMDD`` (BigQuery form) or ISO."""
    if value is None:
        return None
    s = str(value).strip()
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return _as_date(s)


def _as_dict(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _significance(llm_features: Any, llm_status: Any) -> float | None:
    if llm_status != "success":
        return None
    feats = _as_dict(llm_features)
    if not feats:
        return None
    v = feats.get("significance")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _patent_row(record: Any) -> dict | None:
    """Shape a DB patent record into the dict ``compute_indicators`` expects.

    Returns None when the publication date is missing/unparseable (no honest
    knowable timestamp → cannot be used look-ahead safely).
    """
    payload = _as_dict(record["extra_payload"]) or {}
    pub = _parse_yyyymmdd(payload.get("publication_date"))
    if pub is None:
        return None
    return {
        # both keys = publication date → window + momentum on the publication timeline
        "application_date": pub,
        "publication_date": pub,
        "is_new_category": bool(record["is_new_category"]),
        "tech_category": record["tech_category"],
        "significance": _significance(record["llm_features"], record["llm_status"]),
    }


async def _price_series_for(market: Any, stock_id: int, start: date, end: date) -> PriceSeries:
    rows = await market.list_ohlcv_between(stock_id=stock_id, start_date=start, end_date=end)
    pairs = [(r["trade_date"], float(r["close"])) for r in rows if r["close"] is not None]
    return PriceSeries.from_pairs(pairs)


async def load_patent_dataset(
    pool: Any,
    *,
    stock_ids: list[int],
    start: date,
    end: date,
    benchmark_stock_id: int | None = None,
    prices_override: dict[int, PriceSeries] | None = None,
    benchmark_override: PriceSeries | None = None,
    lookback_days: int = 60,
    horizon_sessions: int = 5,
    neutral_band_pct: float = 0.3,
    signal_step: int = 5,
    min_observations: int = 1,
    xs_normalize: str = "none",
    exclude_features: frozenset[str] = frozenset(),
) -> Dataset:
    """Fetch patents (full history per stock) + prices, and build a Dataset."""
    from signal_alpha_data_access.repositories.raw_details import RawDetailRepository

    price_end = end + timedelta(days=horizon_sessions * 3 + 10)
    since = start - timedelta(days=lookback_days + 5)
    use_csv_prices = prices_override is not None

    patent_rows_by_stock: dict[int, list[dict]] = {}
    prices_by_stock: dict[int, PriceSeries] = {}
    signal_dates_by_stock: dict[int, list[date]] = {}

    async with pool.acquire() as conn:
        raw = RawDetailRepository(conn)
        market = None
        if not use_csv_prices:
            from signal_alpha_data_access.repositories.market_data import MarketDataRepository

            market = MarketDataRepository(conn)

        if benchmark_override is not None:
            benchmark = benchmark_override
        elif benchmark_stock_id is not None and market is not None:
            benchmark = await _price_series_for(market, benchmark_stock_id, since, price_end)
        else:
            benchmark = None

        for stock_id in stock_ids:
            # Full history: publications in the price window come from filings ~18mo
            # earlier, so a date-bounded fetch would miss them.
            records = await raw.list_patent_details_by_stock(stock_id=stock_id, since_date=None)
            shaped = [_patent_row(r) for r in records]
            patent_rows_by_stock[stock_id] = [r for r in shaped if r is not None]

            if use_csv_prices:
                prices = prices_override.get(stock_id) or PriceSeries.from_pairs([])
            else:
                prices = await _price_series_for(market, stock_id, since, price_end)
            prices_by_stock[stock_id] = prices
            in_window = [d for d in prices.dates if start <= d <= end]
            signal_dates_by_stock[stock_id] = weekly_signal_dates(in_window, step=signal_step)

    return build_dataset(
        patent_rows_by_stock=patent_rows_by_stock,
        prices_by_stock=prices_by_stock,
        signal_dates_by_stock=signal_dates_by_stock,
        benchmark=benchmark,
        lookback_days=lookback_days,
        horizon_sessions=horizon_sessions,
        neutral_band_pct=neutral_band_pct,
        min_observations=min_observations,
        xs_normalize=xs_normalize,
        exclude_features=exclude_features,
    )


async def load_from_env(
    *,
    database_url: str,
    tickers: list[str],
    start: date,
    end: date,
    benchmark_ticker: str | None = None,
    prices_csv: str | None = None,
    **kwargs: Any,
) -> Dataset:
    """Open a pool, resolve tickers, and load the patent dataset; closes the pool after.

    Mirrors ``datalab_db.load_from_env`` (CSV-price path keeps ohlcv_data untouched).
    """
    from signal_alpha_data_access import DatabaseSettings, create_pool

    prices_override = benchmark_override = None
    if prices_csv is not None:
        from .prices_csv import load_prices_csv

        by_ticker = load_prices_csv(prices_csv)
        benchmark_override = by_ticker.get(benchmark_ticker) if benchmark_ticker else None

    pool = await create_pool(DatabaseSettings(database_url=database_url))
    try:
        async with pool.acquire() as conn:
            id_by_ticker = await resolve_stock_ids(conn, tickers)
        missing = [t for t in tickers if t not in id_by_ticker]
        if missing:
            raise ValueError(f"tickers not found in stocks table: {missing}")
        stock_ids = [id_by_ticker[t] for t in tickers]

        if prices_csv is not None:
            prices_override = {
                id_by_ticker[t]: by_ticker[t] for t in tickers if t in by_ticker
            }

        return await load_patent_dataset(
            pool,
            stock_ids=stock_ids,
            start=start,
            end=end,
            benchmark_stock_id=(
                id_by_ticker.get(benchmark_ticker)
                if (benchmark_ticker and prices_csv is None)
                else None
            ),
            prices_override=prices_override,
            benchmark_override=benchmark_override,
            **kwargs,
        )
    finally:
        await pool.close()


__all__ = ["load_from_env", "load_patent_dataset"]
