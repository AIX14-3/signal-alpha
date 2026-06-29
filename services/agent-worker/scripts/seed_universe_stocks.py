"""Idempotently upsert a stock universe into the ``stocks`` table.

Reads a universe file (JSON list or CSV with a ``ticker`` column) and
INSERT ... ON CONFLICT (ticker) DO NOTHING so existing rows are untouched.
Data op only (no DDL). backfill/enrich/the ML harness all resolve
ticker→stock_id from this table.

Two flows:
  * Patent track — pass ``--patterns patent_assignee_patterns.json`` to restrict
    the seeded tickers to the reviewed pattern keys (final patent universe).
  * Hiring / broad universe — omit ``--patterns`` to seed every row in the file
    (e.g. the KOSPI top-200 from ``build_kospi_universe.py``).

Seeded rows are ``is_target=FALSE`` (the team's collectors only act on
is_target=TRUE, so this is a harmless shared-table addition) and ``is_active=TRUE``.

    uv run python scripts/seed_universe_stocks.py --universe universe_kospi200.json
    uv run python scripts/seed_universe_stocks.py --universe universe.json \
        --patterns patent_assignee_patterns.json
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _norm_market(m: str) -> str | None:
    m = (m or "").upper()
    if "KOSPI" in m:
        return "KOSPI"
    if "KOSDAQ" in m:
        return "KOSDAQ"
    return None  # KONEX / unknown → stocks CHECK only allows KOSPI/KOSDAQ


def _load_universe(path: Path) -> list[dict]:
    """Load universe records from a JSON list or a CSV (header with ``ticker``)."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    return json.loads(path.read_text(encoding="utf-8"))


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Upsert a stock universe into stocks")
    ap.add_argument("--universe", required=True, help="universe JSON/CSV (must have ticker)")
    ap.add_argument("--patterns", default=None,
                    help="optional patent_assignee_patterns.json — restrict to its keys (patent track)")
    args = ap.parse_args(argv)

    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass
    import asyncpg

    records = _load_universe(Path(args.universe))
    uni = {str(r["ticker"]).zfill(6): r for r in records if r.get("ticker")}

    if args.patterns:
        keys = set(json.loads(Path(args.patterns).read_text(encoding="utf-8")).keys())
        tickers = sorted(t for t in keys if t in uni) or sorted(keys)
    else:
        tickers = sorted(uni.keys())

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    inserted = skipped = bad = 0
    try:
        for t in tickers:
            u = uni.get(t, {})
            market = _norm_market(u.get("market", ""))
            if market is None:
                print(f"  [skip-market] {t} {u.get('name','')} market={u.get('market','')!r} (not KOSPI/KOSDAQ)")
                bad += 1
                continue
            name = (u.get("name") or t)[:100]
            short = u.get("short_name") or None
            if short == u.get("name"):
                short = None  # redundant alias — name already matches
            sector = (u.get("sector") or u.get("industry") or None)
            row = await conn.fetchrow(
                """
                INSERT INTO stocks (ticker, name, short_name, market, sector, is_active, is_target)
                VALUES ($1, $2, $3, $4, $5, TRUE, FALSE)
                ON CONFLICT (ticker) DO NOTHING
                RETURNING id
                """,
                t, name, (short[:100] if short else None), market,
                (sector[:100] if sector else None),
            )
            if row is None:
                skipped += 1
            else:
                inserted += 1
                print(f"  [insert] {t} {name} ({market})")
    finally:
        await conn.close()
    print(f"[done] inserted={inserted} already-present={skipped} skipped-market={bad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
