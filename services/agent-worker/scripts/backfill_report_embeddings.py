"""Backfill EMBED_REPORT tasks for parsed reports that have no RAG chunks yet (#709).

Enqueues one ``embed_report`` task per ``report_raw_details`` row that parsed
successfully but is absent from ``report_chunks``, so the worker embeds them under its
normal fairness caps / retries / idempotent ``ON CONFLICT``. No embedding credentials are
needed here — the worker holds them. Enqueue (not direct-embed) so the same caps and
dedupe that govern live traffic also govern the backfill.

Run (from services/agent-worker):
    $env:DATABASE_URL="postgresql://.../collection"
    python scripts/backfill_report_embeddings.py --limit 500
    python scripts/backfill_report_embeddings.py --stock-id 12 --limit 50
    python scripts/backfill_report_embeddings.py --limit 5 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg

from signal_alpha_data_access.repositories import ProcessingQueueRepository

from app.orchestrator.queue.task_types import EMBED_REPORT

_CANDIDATES_SQL = """
    SELECT rrd.raw_document_id, rd.stock_id, s.ticker AS stock_code
    FROM report_raw_details rrd
    JOIN raw_documents rd ON rd.id = rrd.raw_document_id
    JOIN stocks s         ON s.id = rd.stock_id
    WHERE rrd.parsing_status = 'success'
      AND NOT EXISTS (
          SELECT 1 FROM report_chunks rc
          WHERE rc.report_raw_detail_id = rrd.raw_document_id
      )
      AND ($1::BIGINT IS NULL OR rd.stock_id = $1)
    ORDER BY rrd.publish_date DESC NULLS LAST
    LIMIT $2
"""


async def main(*, stock_id: int | None, limit: int, dry_run: bool) -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise SystemExit("DATABASE_URL is required (collection DB).")

    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(_CANDIDATES_SQL, stock_id, limit)
        print(f"candidates (parsed, no chunks): {len(rows)}")
        if dry_run:
            for row in rows:
                print(f"  dry-run raw_document_id={row['raw_document_id']} stock_code={row['stock_code']}")
            return

        queue = ProcessingQueueRepository(conn)
        enqueued = 0
        for row in rows:
            raw_document_id = int(row["raw_document_id"])
            await queue.enqueue(
                stock_id=int(row["stock_id"]),
                task_type=EMBED_REPORT,
                priority="batch",
                source_raw_ids=[raw_document_id],
                task_context={
                    "raw_document_id": raw_document_id,
                    "stock_code": str(row["stock_code"]),
                },
                dedupe=True,
            )
            enqueued += 1
        print(f"enqueued embed_report tasks: {enqueued}")
    finally:
        await conn.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill embed_report tasks for report RAG chunks.")
    parser.add_argument("--stock-id", type=int, default=None, help="Limit to one stock_id.")
    parser.add_argument("--limit", type=int, default=500, help="Max reports to enqueue (default 500).")
    parser.add_argument("--dry-run", action="store_true", help="List candidates without enqueuing.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(stock_id=args.stock_id, limit=args.limit, dry_run=args.dry_run))
