"""Backfill DataLab search trends for a date range using the shipping collector.

Sources the category/keyword collection plan from the DB (not the static
``category_registry.json``, which is a stale subset): it reads the DISTINCT
(category, keyword) pairs already collected for the target tickers' categories, so
a backfill reproduces exactly what the live pipeline collects — including the
per-stock categories (14-28) the registry file omits. Calls ``DataLabCollector.run``
once per category per calendar year (Naver's daily API is happiest with <=1-year
ranges). Re-running is safe: duplicate observations are skipped (UniqueViolation).

Prerequisites:
  - .env with NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (https://developers.naver.com,
    enable the DataLab "search trend" API) and DATABASE_URL.
  - Categories + ``datalab_category_stocks`` mapping seeded (verified: they are).

    python scripts/backfill_datalab.py --start-year 2021 --end-year 2023
    python scripts/backfill_datalab.py --tickers 005930 --start-year 2021 --end-year 2021

NOTE: the plan is derived from previously-collected keywords. A category that was
never collected (no rows yet) contributes nothing here; seed at least one
collection (or add its keywords) before backfilling it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Make ``app`` importable when run as ``python scripts/backfill_datalab.py``
# (Python puts scripts/ on sys.path, not the service root). parents[1] = agent-worker.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def _collection_plan(conn, tickers: list[str]) -> list[dict]:
    """(category, keyword) collection plan for the target tickers' categories.

    Unions two keyword sources so it covers every mapped category:
    - ``datalab_category_keywords`` — the canonical polarity-tagged registry (the
      only source for stocks never collected yet, e.g. a newly-added universe), and
    - ``datalab_raw_details`` — keywords already collected (covers the original
      categories whose keywords came from category_registry.json, not the DB table).

    Returns ``[{category_id, category_name, keyword_group, keywords:[...]}]``.
    """
    rows = await conn.fetch(
        """
        WITH kw AS (
            SELECT category_id, keyword FROM datalab_category_keywords
            UNION
            SELECT category_id, keyword FROM datalab_raw_details
        )
        SELECT kw.category_id, c.name AS category_name, kw.keyword
        FROM kw
        JOIN datalab_categories c ON c.id = kw.category_id
        WHERE kw.category_id IN (
            SELECT dcs.category_id
            FROM datalab_category_stocks dcs
            JOIN stocks s ON s.id = dcs.stock_id
            WHERE s.ticker = ANY($1::text[])
        )
        GROUP BY kw.category_id, c.name, kw.keyword
        ORDER BY kw.category_id, kw.keyword
        """,
        tickers,
    )
    by_cat: dict[int, dict] = {}
    for r in rows:
        cat = by_cat.setdefault(
            int(r["category_id"]),
            {
                "category_id": int(r["category_id"]),
                "category_name": r["category_name"],
                "keyword_group": r["category_name"],
                "keywords": [],
            },
        )
        cat["keywords"].append(r["keyword"])
    return list(by_cat.values())


async def _run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill DataLab by category (DB-sourced plan)")
    parser.add_argument("--start-year", type=int, default=2021)
    parser.add_argument("--end-year", type=int, default=2023)
    parser.add_argument("--tickers", default="005930,000660,035420", help="comma-separated")
    args = parser.parse_args(argv)

    try:  # repo-root .env supplies DATABASE_URL / NAVER_CLIENT_ID / NAVER_CLIENT_SECRET
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    database_url = os.environ.get("DATABASE_URL")
    client_id = os.environ.get("NAVER_CLIENT_ID")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    if not (client_id and client_secret):
        raise SystemExit("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET are required")

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    from app.clients.naver_datalab_client import NaverDataLabClient
    from app.collectors.datalab import DataLabCollector
    from signal_alpha_data_access import DatabaseSettings, create_pool

    client = NaverDataLabClient(client_id=client_id, client_secret=client_secret)
    pool = await create_pool(DatabaseSettings(database_url=database_url))
    collector = DataLabCollector(pool=pool, client=client)

    total_inserted = total_skipped = total_failed = 0
    try:
        async with pool.acquire() as conn:
            categories = await _collection_plan(conn, tickers)
        print(f"[plan] {len(categories)} categories from tickers {tickers}")
        for category in categories:
            for year in range(args.start_year, args.end_year + 1):
                start = f"{year}-01-01"
                end = f"{year}-12-31"
                # Retry transient network blips (e.g. RemoteDisconnected) so one
                # dropped connection can't abort a 90-min backfill. Re-running a
                # category is idempotent (already-collected rows skip on conflict).
                result = None
                for attempt in range(5):
                    try:
                        result = await collector.run(
                            category_id=category["category_id"],
                            category_name=category["category_name"],
                            keyword_group=category["keyword_group"],
                            keywords=category["keywords"],
                            start_date=start,
                            end_date=end,
                            time_unit="date",
                        )
                        break
                    except Exception as exc:  # noqa: BLE001 — transient net errors
                        if attempt == 4:
                            print(
                                f"  cat {category['category_id']:>2} "
                                f"{category['category_name']:<16} {year}: GAVE UP ({exc})"
                            )
                        else:
                            await asyncio.sleep(2.0 * (attempt + 1))
                if result is None:
                    total_failed += 1
                    continue
                total_inserted += result["inserted_count"]
                total_skipped += result["skipped_count"]
                total_failed += result["failed_count"]
                print(
                    f"  cat {category['category_id']:>2} {category['category_name']:<16} "
                    f"{year}: +{result['inserted_count']} "
                    f"skip {result['skipped_count']} fail {result['failed_count']}"
                )
    finally:
        await pool.close()

    print(
        f"[done] inserted={total_inserted} skipped={total_skipped} failed={total_failed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
