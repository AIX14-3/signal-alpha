"""DB adapter: fetch real DataLab + price rows and build a bake-off Dataset.

This is the production-data entry that replaces ``datalab_demo`` once 2021-2023
DataLab and OHLCV are loaded. It reuses the SHIPPING repositories
(``RawDetailRepository`` for DataLab, ``MarketDataRepository`` for prices) so
there is no new SQL to keep in sync, then hands plain rows to the pure
``datalab_dataset.build_dataset``.

Not exercised by unit tests (it needs a live DB); the transformation it feeds is
fully covered by ``test_ml_datalab_dataset``. Run via:

    DATABASE_URL=... python -m app.ml.research.bakeoff --source datalab-db \
        --tickers 005930,000660,035420 --start 2021-01-01 --end 2023-12-31
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# NOTE: ``signal_alpha_data_access`` is imported LAZILY inside functions (matching
# app/core/database.py) so this module imports even where that workspace package
# isn't installed (e.g. a bare research env running only the synthetic/demo paths).
from .datalab_dataset import Dataset, PriceSeries, build_dataset, weekly_signal_dates


async def resolve_stock_ids(conn: Any, tickers: list[str]) -> dict[str, int]:
    """Map tickers (e.g. '005930') to stock_id; missing tickers are omitted."""
    rows = await conn.fetch(
        "SELECT id, ticker FROM stocks WHERE ticker = ANY($1::text[])", tickers
    )
    return {r["ticker"]: int(r["id"]) for r in rows}


def _datalab_row(record: Any, weight_by_category: dict[int, float]) -> dict[str, Any]:
    """Shape a DB DataLab record into the dict ``compute_indicators`` expects.

    Mirrors ``evidence_loaders.datalab_loader._row`` (weight from the category
    map, polarity defaulting to 'demand').
    """
    category_id = int(record["category_id"])
    observed = record["observed_date"]
    return {
        "category_id": category_id,
        "weight": weight_by_category.get(category_id, 1.0),
        "keyword": record["keyword"],
        "keyword_group": record["keyword_group"],
        "observed_date": observed.isoformat() if hasattr(observed, "isoformat") else observed,
        "search_index": _f(record["search_index"]),
        "change_pct": _f(record["change_pct"]),
        "is_spike": bool(record["is_spike"]),
        "polarity": (record["polarity"] if "polarity" in record else None) or "demand",
    }


async def _price_series_for(market: MarketDataRepository, stock_id: int, start: date, end: date) -> PriceSeries:
    rows = await market.list_ohlcv_between(stock_id=stock_id, start_date=start, end_date=end)
    pairs = [(r["trade_date"], _f(r["close"])) for r in rows if r["close"] is not None]
    return PriceSeries.from_pairs(pairs)


async def load_datalab_dataset(
    pool: Any,
    *,
    stock_ids: list[int],
    start: date,
    end: date,
    benchmark_stock_id: int | None = None,
    prices_override: dict[int, PriceSeries] | None = None,
    benchmark_override: PriceSeries | None = None,
    lookback_days: int = 30,
    horizon_sessions: int = 5,
    neutral_band_pct: float = 0.3,
    signal_step: int = 5,
    min_observations: int = 3,
) -> Dataset:
    """Fetch DataLab (always from DB) + prices, and build a Dataset.

    Prices come from ``prices_override``/``benchmark_override`` when given (the
    local-CSV path, so ``ohlcv_data`` is never touched); otherwise they are pulled
    from the DB with a forward buffer so the last in-window signals still have
    ``horizon_sessions`` of future closes to label against.
    """
    from signal_alpha_data_access.repositories.raw_details import RawDetailRepository

    price_end = end + timedelta(days=horizon_sessions * 3 + 10)  # buffer for forward label
    since = start - timedelta(days=lookback_days + 5)
    use_csv_prices = prices_override is not None

    datalab_rows_by_stock: dict[int, list[dict]] = {}
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
            categories = await raw.list_datalab_categories_for_stock(stock_id)
            weight_by_category = {
                int(c["category_id"]): float(c["weight"]) for c in categories
            }
            category_ids = list(weight_by_category)
            records = (
                await raw.list_datalab_details_by_category(
                    category_ids=category_ids, since_date=since
                )
                if category_ids
                else []
            )
            datalab_rows_by_stock[stock_id] = [
                _datalab_row(r, weight_by_category) for r in records
            ]

            if use_csv_prices:
                prices = prices_override.get(stock_id) or PriceSeries.from_pairs([])
            else:
                prices = await _price_series_for(market, stock_id, since, price_end)
            prices_by_stock[stock_id] = prices
            in_window = [d for d in prices.dates if start <= d <= end]
            signal_dates_by_stock[stock_id] = weekly_signal_dates(in_window, step=signal_step)

    return build_dataset(
        datalab_rows_by_stock=datalab_rows_by_stock,
        prices_by_stock=prices_by_stock,
        signal_dates_by_stock=signal_dates_by_stock,
        benchmark=benchmark,
        lookback_days=lookback_days,
        horizon_sessions=horizon_sessions,
        neutral_band_pct=neutral_band_pct,
        min_observations=min_observations,
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
    """Open a pool, resolve tickers, and load the dataset; closes the pool after.

    With ``prices_csv`` the prices come from the local CSV (keyed by ticker, mapped
    to stock_id) and ``ohlcv_data`` is never read; the benchmark is the CSV series
    named ``benchmark_ticker`` (e.g. an index like ``KS11`` that isn't a stock row).
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
            # Only stock tickers need a stock_id; a CSV benchmark index may not be one.
            id_by_ticker = await resolve_stock_ids(conn, tickers)
        missing = [t for t in tickers if t not in id_by_ticker]
        if missing:
            raise ValueError(f"tickers not found in stocks table: {missing}")
        stock_ids = [id_by_ticker[t] for t in tickers]

        if prices_csv is not None:
            prices_override = {
                id_by_ticker[t]: by_ticker[t] for t in tickers if t in by_ticker
            }

        return await load_datalab_dataset(
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


def _f(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
