"""Seed hiring job-function taxonomy + per-stock exposure weights (C4).

Two steps, both idempotent (UPSERT):
  1. Upsert the canonical function taxonomy into ``hiring_job_functions``.
  2. For each active stock with hiring data, derive its function EXPOSURE from its
     OWN posting mix — classify every posting title, take each function's share of
     the stock's classified postings, and store shares >= --min-share as weights in
     ``hiring_job_function_stocks``.

This is the data-driven "sector-based v1" seed: a company's own hiring mix is a
fair proxy for which job functions it depends on. The analyzer then propagates
PEER demand for those functions to the stock (see job_functions.aggregate_sector_demand).

Run after migration 020, whenever the hiring corpus has grown materially:
    python seed_hiring_job_functions.py            # apply
    python seed_hiring_job_functions.py --dry-run   # print, write nothing
    python seed_hiring_job_functions.py --min-share 0.15
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import asyncpg  # type: ignore[import]

sys.path.insert(0, str(Path(__file__).parent))

from app.analyzers.hiring.job_functions import LABELS, classify_job_function

ROOT = Path(__file__).resolve().parents[2]


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}


def resolve_ssl(host: str) -> Any:
    """SSL mode for asyncpg.connect.

    Managed Postgres (Supabase) needs ``ssl='require'``; a local Docker Postgres
    rejects SSL upgrades. Default by host so local testing works without flags;
    ``DB_SSL`` overrides (e.g. ``DB_SSL=disable`` / ``DB_SSL=require``).
    """
    override = os.getenv("DB_SSL")
    if override:
        return False if override.lower() in {"disable", "off", "false", "0", "no"} else override
    return False if host in _LOCAL_HOSTS else "require"


def parse_dsn(dsn: str) -> dict[str, Any]:
    match = re.match(
        r"^postgres(?:ql)?://(?P<user>[^:]+):(?P<password>.*)@"
        r"(?P<host>[^:/@]+):(?P<port>\d+)/(?P<db>[^?]+)",
        dsn,
    )
    if not match:
        raise ValueError("Could not parse DATABASE_URL")
    return {
        "user": unquote(match.group("user")),
        "password": unquote(match.group("password")),
        "host": match.group("host"),
        "port": int(match.group("port")),
        "database": match.group("db"),
    }


async def upsert_functions(conn: asyncpg.Connection) -> dict[str, int]:
    ids: dict[str, int] = {}
    for function_key, label in LABELS.items():
        function_id = await conn.fetchval(
            """
            INSERT INTO hiring_job_functions (function_key, label, is_active)
            VALUES ($1, $2, TRUE)
            ON CONFLICT (function_key) DO UPDATE
            SET label = EXCLUDED.label, is_active = TRUE, updated_at = NOW()
            RETURNING id
            """,
            function_key, label,
        )
        ids[function_key] = function_id
    return ids


def function_shares(keywords: list[str | None], *, min_share: float) -> dict[str, float]:
    counts: dict[str, int] = {}
    classified = 0
    for keyword in keywords:
        function_key = classify_job_function(keyword)
        if function_key is None:
            continue
        counts[function_key] = counts.get(function_key, 0) + 1
        classified += 1
    if classified == 0:
        return {}
    shares = {k: round(v / classified, 2) for k, v in counts.items()}
    return {k: s for k, s in shares.items() if s >= min_share}


async def main() -> None:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-share", type=float, default=0.1, help="Min posting share to map a function.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan; write nothing.")
    args = parser.parse_args()

    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required.")
    params = parse_dsn(dsn)
    conn = await asyncpg.connect(**params, ssl=resolve_ssl(params["host"]), statement_cache_size=0)
    try:
        function_ids = {} if args.dry_run else await upsert_functions(conn)
        stocks = await conn.fetch(
            """
            SELECT DISTINCT s.id, s.ticker, s.name
            FROM stocks s
            INNER JOIN hiring_raw_details h ON h.stock_id = s.id
            WHERE s.is_active = TRUE
            ORDER BY s.id
            """
        )
        print("\nSEED HIRING JOB FUNCTIONS")
        print("=" * 60)
        print(f"functions={len(LABELS)} stocks_with_hiring={len(stocks)} min_share={args.min_share}")
        mapped = 0
        for stock in stocks:
            keywords = [
                r["keyword"]
                for r in await conn.fetch(
                    "SELECT keyword FROM hiring_raw_details WHERE stock_id = $1", stock["id"]
                )
            ]
            shares = function_shares(keywords, min_share=args.min_share)
            print(f"  {stock['ticker']} {stock['name']}: {shares or '(no classifiable postings)'}")
            if args.dry_run or not shares:
                continue
            for function_key, weight in shares.items():
                await conn.execute(
                    """
                    INSERT INTO hiring_job_function_stocks (job_function_id, stock_id, weight)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (job_function_id, stock_id) DO UPDATE SET weight = EXCLUDED.weight
                    """,
                    function_ids[function_key], stock["id"], weight,
                )
                mapped += 1
        print("-" * 60)
        print(f"applied={not args.dry_run} mappings_written={mapped}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
