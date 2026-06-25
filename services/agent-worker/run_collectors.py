"""Run Patent and DataLab collectors from DB-driven targets.

No stock tickers, applicant names, DB credentials, or collection windows are
hardcoded here. Targets come from the database; dates come from CLI args or the
collector defaults.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import]

sys.path.insert(0, str(Path(__file__).parent))

from app.clients.kipris_client import KiprisClient
from app.clients.naver_datalab_client import NaverDataLabClient
from app.collectors.datalab import DataLabCollector
from app.collectors.patent import PatentCollector
# Canonical DSN/SSL helpers (re-exported so existing `from run_collectors import
# parse_dsn` importers — e.g. run_baseline.py — keep working).
from app.core.dsn import parse_dsn, resolve_ssl

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = Path(__file__).parent / "category_registry.json"


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def datalab_date(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("/", "-")


def patent_date(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("-", "").replace("/", "")


async def fetch_patent_targets(conn: asyncpg.Connection, ticker: str | None) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT
            s.id AS stock_id,
            s.ticker,
            s.name AS stock_name,
            d.corp_name,
            d.corp_name_eng,
            d.stock_name AS dart_stock_name
        FROM stocks s
        LEFT JOIN dart_corp_codes d ON d.stock_id = s.id AND d.is_active = TRUE
        WHERE s.is_active = TRUE
          AND ($1::text IS NULL OR s.ticker = $1)
        ORDER BY s.id
        """,
        ticker,
    )
    targets: list[dict[str, Any]] = []
    for row in rows:
        applicant_names = [
            value
            for value in (row["corp_name"], row["corp_name_eng"], row["dart_stock_name"])
            if value and value != row["stock_name"]
        ]
        targets.append(
            {
                "stock_id": row["stock_id"],
                "ticker": row["ticker"],
                "stock_name": row["stock_name"],
                "applicant_names": applicant_names,
            }
        )
    return targets


async def fetch_datalab_categories(conn: asyncpg.Connection, ticker: str | None) -> list[dict[str, Any]]:
    active_rows = await conn.fetch(
        """
        SELECT DISTINCT dc.id
        FROM datalab_categories dc
        JOIN datalab_category_stocks dcs ON dcs.category_id = dc.id
        JOIN stocks s ON s.id = dcs.stock_id AND s.is_active = TRUE
        WHERE dc.is_active = TRUE
          AND ($1::text IS NULL OR s.ticker = $1)
        """,
        ticker,
    )
    active_ids = {row["id"] for row in active_rows}

    categories: list[dict[str, Any]] = []
    has_keyword_table = await conn.fetchval("SELECT to_regclass('public.datalab_category_keywords') IS NOT NULL")
    if has_keyword_table:
        rows = await conn.fetch(
            """
            SELECT
                dc.id AS category_id,
                dc.name AS category_name,
                dck.keyword_group,
                array_agg(dck.keyword ORDER BY dck.keyword) AS keywords
            FROM datalab_categories dc
            JOIN datalab_category_stocks dcs ON dcs.category_id = dc.id
            JOIN stocks s ON s.id = dcs.stock_id AND s.is_active = TRUE
            JOIN datalab_category_keywords dck ON dck.category_id = dc.id AND dck.is_active = TRUE
            WHERE dc.is_active = TRUE
              AND ($1::text IS NULL OR s.ticker = $1)
            GROUP BY dc.id, dc.name, dck.keyword_group
            ORDER BY dc.id
            """,
            ticker,
        )
        if rows:
            categories.extend(dict(row) for row in rows)

    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    loaded_ids = {category["category_id"] for category in categories}
    categories.extend(
        category
        for category in registry
        if category.get("category_id") in active_ids
        and category.get("category_id") not in loaded_ids
    )
    return categories


async def run_once(args: argparse.Namespace) -> None:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required.")
    params = parse_dsn(dsn)
    pool = await asyncpg.create_pool(
        **params,
        min_size=1,
        max_size=max(2, int(os.getenv("COLLECTOR_DB_POOL_MAX", "5"))),
        ssl=resolve_ssl(params["host"]),
        statement_cache_size=0,
    )
    try:
        collector_ver = os.getenv("COLLECTOR_VERSION", "1.0")

        if not args.datalab_only:
            kipris_client = KiprisClient(api_key=os.getenv("KIPRIS_API_KEY", ""))
            patent_collector = PatentCollector(pool=pool, client=kipris_client, collector_ver=collector_ver)
            async with pool.acquire() as conn:
                patent_targets = await fetch_patent_targets(conn, args.ticker)
            print("\nPATENT COLLECTOR")
            print("=" * 60)
            for target in patent_targets:
                print(f"\n> {target['ticker']} {target['stock_name']}")
                result = await patent_collector.run(
                    stock_id=target["stock_id"],
                    stock_code=target["ticker"],
                    stock_name=target["stock_name"],
                    applicant_names=target["applicant_names"],
                    start_date=patent_date(args.start_date),
                    end_date=patent_date(args.end_date),
                )
                print(result)

        if not args.patent_only:
            naver_client = NaverDataLabClient(
                client_id=os.getenv("NAVER_CLIENT_ID", ""),
                client_secret=os.getenv("NAVER_CLIENT_SECRET", ""),
            )
            datalab_collector = DataLabCollector(pool=pool, client=naver_client, collector_ver=collector_ver)
            async with pool.acquire() as conn:
                categories = await fetch_datalab_categories(conn, args.ticker)
            print("\nDATALAB COLLECTOR")
            print("=" * 60)
            for category in categories:
                print(f"\n> {category['category_name']} ({len(category['keywords'])} keywords)")
                result = await datalab_collector.run(
                    category_id=category["category_id"],
                    category_name=category["category_name"],
                    keyword_group=category["keyword_group"],
                    keywords=list(category["keywords"]),
                    start_date=datalab_date(args.start_date),
                    end_date=datalab_date(args.end_date),
                )
                print(result)
    finally:
        await pool.close()


async def main() -> None:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", help="Limit collection to one ticker.")
    parser.add_argument("--start-date", help="YYYY-MM-DD. Defaults to collector daily window.")
    parser.add_argument("--end-date", help="YYYY-MM-DD. Defaults to collector daily window.")
    parser.add_argument("--patent-only", action="store_true")
    parser.add_argument("--datalab-only", action="store_true")
    parser.add_argument("--loop", action="store_true", help="Repeat collection until interrupted.")
    parser.add_argument("--interval-seconds", type=int, default=3600)
    args = parser.parse_args()

    while True:
        await run_once(args)
        if not args.loop:
            break
        await asyncio.sleep(args.interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
