"""Build hiring baselines (hiring_baseline) from '{company} 채용' DataLab trends.

Quarterly-ish job: reads is_target stocks, queries Naver DataLab for ~3 years of
monthly search trends per company's hiring keywords, derives seasonal factors, and
UPSERTs one row per stock into hiring_baseline.

No tickers, credentials, or windows are hardcoded — targets come from the DB,
credentials/window from env and CLI.

Usage:
    python run_baseline.py                 # all is_target stocks
    python run_baseline.py --ticker 005930 # single stock
    python run_baseline.py --years 3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import]

sys.path.insert(0, str(Path(__file__).parent))

from app.baseline.hiring_baseline_builder import DataLabBaselineCollector, resolve_naver_client
from run_collectors import load_env, parse_dsn


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}


def resolve_ssl(host: str) -> Any:
    """SSL mode for asyncpg: managed Postgres (Supabase) needs 'require'; a local
    Docker Postgres rejects SSL. Default by host; ``DB_SSL`` env overrides."""
    override = os.getenv("DB_SSL")
    if override:
        return False if override.lower() in {"disable", "off", "false", "0", "no"} else override
    return False if host in _LOCAL_HOSTS else "require"


async def fetch_baseline_targets(conn: asyncpg.Connection, ticker: str | None) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT
            s.id AS stock_id,
            s.ticker,
            s.name AS company_name,
            COALESCE(s.sector, 'tech') AS category,
            s.short_name
        FROM stocks s
        WHERE s.is_target = TRUE
          AND ($1::text IS NULL OR s.ticker = $1)
        ORDER BY s.id
        """,
        ticker,
    )
    return [dict(row) for row in rows]


async def run_once(args: argparse.Namespace) -> None:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required.")

    years = args.years if args.years is not None else int(os.getenv("BASELINE_YEARS", "3"))
    min_months = int(os.getenv("BASELINE_MIN_MONTHS_PER_QUARTER", "2"))

    params = parse_dsn(dsn)
    pool = await asyncpg.create_pool(
        **params,
        min_size=1,
        max_size=max(2, int(os.getenv("COLLECTOR_DB_POOL_MAX", "5"))),
        ssl=resolve_ssl(params["host"]),
        statement_cache_size=0,
    )
    try:
        async with pool.acquire() as conn:
            targets = await fetch_baseline_targets(conn, args.ticker)

        if not targets:
            print("No is_target stocks found (check ticker / 015·016 migrations).")
            return

        client = resolve_naver_client()
        builder = DataLabBaselineCollector(
            pool=pool,
            client=client,
            years=years,
            min_months_per_quarter=min_months,
        )

        print("\nHIRING BASELINE BUILDER")
        print("=" * 60)
        print(f"targets={len(targets)}  years={years}  min_months_per_quarter={min_months}")

        results = await builder.build(targets=targets)

        ok = sum(1 for r in results if r["status"] == "success")
        failed = len(results) - ok
        for r in results:
            if r["status"] == "success":
                s = r["stats"]
                print(
                    f"  ✓ {r['company_name']} (stock_id={r['stock_id']}): "
                    f"obs={s.observations} avg={s.avg_search_volume} "
                    f"q=[{s.q1_factor},{s.q2_factor},{s.q3_factor},{s.q4_factor}]"
                )
            else:
                print(f"  ✗ {r['company_name']} (stock_id={r['stock_id']}): {r.get('error')}")
        print("-" * 60)
        print(f"UPSERTed {ok} baseline(s), {failed} failed.")
    finally:
        await pool.close()


async def main() -> None:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", help="Limit to one ticker.")
    parser.add_argument("--years", type=int, default=None, help="Lookback years (default 3 / BASELINE_YEARS).")
    args = parser.parse_args()
    await run_once(args)


if __name__ == "__main__":
    asyncio.run(main())
