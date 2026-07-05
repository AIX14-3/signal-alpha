"""DB adapter: fetch real HIRING postings + CSV prices and build a bake-off Dataset.

Mirrors ``datalab_db`` but for hiring. Hiring postings are few (~330 across the 3
target stocks / 8 years), so we read them with a tiny direct asyncpg query rather
than pulling in the workspace repositories — keeping the research path importable
in a bare env. Prices come from a local CSV (``--prices-csv``), never ``ohlcv_data``.

The knowledge time of a posting is its KST publish date; we read
``(published_at AT TIME ZONE 'Asia/Seoul')::date`` as ``observed_date`` so the
point-in-time windowing in ``hiring_dataset`` matches how the data was stored.

Run:
    DATABASE_URL=... python -m app.ml.research.bakeoff --source hiring-db \
        --tickers 005930,000660,035420 --start 2016-01-01 --end 2023-12-31 \
        --benchmark KS11 --prices-csv prices_hiring.csv \
        --lookback 90 --horizon 20 --signal-step 20
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any

from .hiring_dataset import build_dataset, weekly_signal_dates
from .prices_csv import load_prices_csv

_CORP_TOKENS = ("(주)", "주식회사", "㈜", "(유)", "(재)")


def _norm_name(raw: str) -> str:
    """Normalize a company name for **exact** matching (precision).

    Mirrors ``BaseCollector._clean_company_name`` (strips corporate suffixes) then
    casefolds and removes all whitespace so 'SK 하이닉스' == 'SK하이닉스'. Exact-only:
    'SKC코오롱PI' will NOT collapse to 'SKC', so it cannot be mis-attributed to SKC.
    """
    s = raw or ""
    for tok in _CORP_TOKENS:
        s = s.replace(tok, "")
    return re.sub(r"\s+", "", s).casefold()


def unique_norm_map(rows) -> dict[str, int]:
    """Build {normalized name → stock_id} from (stock_id, name, short_name) rows,
    keeping only keys that map to exactly one stock (ambiguous names are dropped).

    Pure (no DB) so the precision contract is unit-testable: e.g. 'SKC코오롱PI' must
    not resolve to 'SKC', and a name shared by two tickers must not resolve at all.
    """
    norm_to_ids: dict[str, set[int]] = defaultdict(set)
    for sid, name, short_name in rows:
        for val in (name, short_name):
            if val:
                norm_to_ids[_norm_name(val)].add(int(sid))
    norm_to_ids.pop("", None)
    return {k: next(iter(v)) for k, v in norm_to_ids.items() if len(v) == 1}


def _as_duty_list(raw: Any) -> list[str]:
    """Normalize a JSONB ``duty_groups`` value (asyncpg may hand it back as text)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [str(x) for x in raw] if isinstance(raw, list) else []


async def _fetch(
    database_url: str,
    tickers: list[str],
    *,
    precise: bool = False,
    with_duty: bool = False,
):
    """Return (id_by_ticker, hiring_rows_by_stock) from prod (read-only).

    Default (``precise=False``): group postings by the stored ``stock_id`` (the broad
    insert-gate attribution — recall-first, used by the 3-stock baseline).

    ``precise=True``: re-attribute every HIRING posting to a universe ticker by
    **exact normalized** ``source_name`` match (precision-first). Postings whose
    normalized name doesn't uniquely match exactly one universe name/short_name are
    dropped, so mis-attributed rows (e.g. SKC코오롱PI booked under SKC) are excluded.

    ``with_duty=True``: also pull each posting's ``duty_groups`` (job-function names)
    from ``hiring_raw_details.extra_payload`` for the duty-mix features.
    """
    import asyncpg

    duty_sel = ", d.extra_payload->'duty_groups' AS duty_groups" if with_duty else ""
    duty_join = (
        "LEFT JOIN hiring_raw_details d ON d.raw_document_id = r.id" if with_duty else ""
    )

    conn = await asyncpg.connect(database_url)
    try:
        srows = await conn.fetch(
            "SELECT id, ticker, name, short_name FROM stocks WHERE ticker = ANY($1::text[])",
            tickers,
        )
        id_by_ticker = {r["ticker"]: int(r["id"]) for r in srows}
        stock_ids = list(id_by_ticker.values())
        hiring_rows_by_stock: dict[int, list[dict]] = defaultdict(list)

        if not stock_ids:
            return id_by_ticker, hiring_rows_by_stock

        def _row(r) -> dict:
            row = {"observed_date": r["observed_date"]}
            if with_duty:
                row["duty_groups"] = _as_duty_list(r["duty_groups"])
            return row

        if not precise:
            hrows = await conn.fetch(
                f"""
                SELECT r.stock_id,
                       (r.published_at AT TIME ZONE 'Asia/Seoul')::date AS observed_date
                       {duty_sel}
                FROM raw_documents r
                {duty_join}
                WHERE r.source_type = 'HIRING' AND r.stock_id = ANY($1::bigint[])
                ORDER BY r.published_at
                """,
                stock_ids,
            )
            for r in hrows:
                hiring_rows_by_stock[int(r["stock_id"])].append(_row(r))
            return id_by_ticker, hiring_rows_by_stock

        # precise rematch — build a normalized name→stock_id map, dropping ambiguous keys.
        unique_map = unique_norm_map(
            (r["id"], r["name"], r["short_name"]) for r in srows
        )

        hrows = await conn.fetch(
            f"""
            SELECT r.source_name,
                   (r.published_at AT TIME ZONE 'Asia/Seoul')::date AS observed_date
                   {duty_sel}
            FROM raw_documents r
            {duty_join}
            WHERE r.source_type = 'HIRING'
            ORDER BY r.published_at
            """,
        )
        matched = dropped = 0
        for r in hrows:
            sid = unique_map.get(_norm_name(r["source_name"] or ""))
            if sid is None:
                dropped += 1
                continue
            hiring_rows_by_stock[sid].append(_row(r))
            matched += 1
        print(f"[hiring-db precise] rematched {matched} postings to "
              f"{len(hiring_rows_by_stock)} universe stocks; dropped {dropped} "
              f"(ambiguous/non-universe)")
        return id_by_ticker, hiring_rows_by_stock
    finally:
        await conn.close()


async def load_from_env(
    *,
    database_url: str,
    tickers: list[str],
    start: date,
    end: date,
    benchmark_ticker: str | None = None,
    prices_csv: str | None = None,
    lookback_days: int = 90,
    horizon_sessions: int = 20,
    neutral_band_pct: float = 0.3,
    signal_step: int = 20,
    min_observations: int = 2,
    precise_rematch: bool = False,
    feature_set: str = "volume",
    target: str = "direction",
    min_cross_section: int = 6,
) -> Any:
    """Fetch hiring postings (DB) + prices (CSV) and build a Dataset.

    Signal dates are sampled every ``signal_step`` trading days within
    ``[start, end]`` (monthly ≈ 20 by default) to curb the label autocorrelation
    that overlapping h-day forward returns would otherwise create.

    ``precise_rematch`` re-attributes postings to the universe by exact normalized
    ``source_name`` match (precision) instead of the stored stock_id — use it for the
    expanded universe where the broad insert-gate may have mis-attributed names.

    ``feature_set`` selects ``volume`` (default), ``duty``, or ``volume+duty``; the
    duty variants additionally pull ``duty_groups`` for the tech-mix features.
    """
    if not prices_csv:
        raise SystemExit("--prices-csv is required for --source hiring-db (run scripts/backfill_prices_fdr.py)")

    id_by_ticker, hiring_rows_by_stock = await _fetch(
        database_url, tickers, precise=precise_rematch,
        with_duty=feature_set in ("duty", "volume+duty"),
    )
    missing = [t for t in tickers if t not in id_by_ticker]
    if missing:
        raise ValueError(f"tickers not found in stocks table: {missing}")

    by_ticker = load_prices_csv(prices_csv)
    prices_by_stock = {
        id_by_ticker[t]: by_ticker[t] for t in tickers if t in by_ticker
    }
    benchmark = by_ticker.get(benchmark_ticker) if benchmark_ticker else None

    signal_dates_by_stock: dict[int, list[date]] = {}
    for t in tickers:
        sid = id_by_ticker[t]
        prices = prices_by_stock.get(sid)
        in_window = (
            [d for d in prices.dates if start <= d <= end] if prices is not None else []
        )
        signal_dates_by_stock[sid] = weekly_signal_dates(in_window, step=signal_step)

    return build_dataset(
        hiring_rows_by_stock=hiring_rows_by_stock,
        prices_by_stock=prices_by_stock,
        signal_dates_by_stock=signal_dates_by_stock,
        benchmark=benchmark,
        lookback_days=lookback_days,
        horizon_sessions=horizon_sessions,
        neutral_band_pct=neutral_band_pct,
        min_observations=min_observations,
        feature_set=feature_set,
        target=target,
        min_cross_section=min_cross_section,
    )
