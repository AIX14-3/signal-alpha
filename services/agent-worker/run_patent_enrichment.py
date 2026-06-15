"""Enrich pending patents with cached LLM significance features (C3).

A one-time-per-patent enrichment job: it reads each not-yet-enriched patent's
title + abstract, asks Gemini for structured significance features, and caches
them in ``patent_raw_details.llm_features`` (migration 019). The Patent analyzer
later reads those features to let important patents drive the score, and falls
back to the count-based rules for patents that were not (or could not be) enriched.

This is the enrichment boundary tool — NOT a collector (collectors never call an
LLM) and NOT an analyzer (analyzers only read the cache). Reuses the shared Gemini
client and the ``GEMINI_API_KEY``/``GEMINI_MODEL`` env vars.

Usage:
    python run_patent_enrichment.py                 # enrich up to --limit pending patents
    python run_patent_enrichment.py --limit 50      # smaller batch (e.g. an API dry-run)
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
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "data-access"))

from app.clients.gemini_client import GeminiJsonClient
from app.enrichment.patent_features import PatentEnricher
from signal_alpha_data_access.repositories import RawDetailRepository

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


async def main() -> None:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200, help="Max pending patents to enrich.")
    args = parser.parse_args()

    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required.")

    client = GeminiJsonClient()
    conn = await asyncpg.connect(**parse_dsn(dsn), ssl="require", statement_cache_size=0)
    try:
        enricher = PatentEnricher(
            RawDetailRepository(conn), client, batch_limit=args.limit
        )
        stats = await enricher.run()
    finally:
        await conn.close()

    print("\nPATENT ENRICHMENT")
    print("=" * 40)
    print(
        f"total={stats['total']} success={stats['success']} "
        f"failed={stats['failed']} skipped={stats['skipped']}"
    )


if __name__ == "__main__":
    asyncio.run(main())
