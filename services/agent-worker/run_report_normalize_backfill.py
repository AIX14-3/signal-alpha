"""Inspect or enqueue Report normalize backfill tasks."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker

from mvp_runtime import bootstrap, build_pool, load_env

bootstrap()


async def _run(args: argparse.Namespace) -> None:
    load_env()
    from app.orchestrator.report.normalize_backfill import schedule_report_normalize_backfill

    pool = await build_pool(max_pool_size=2)
    try:
        async with pool.acquire() as connection:
            summary = await schedule_report_normalize_backfill(
                connection,
                stock_code=args.stock_code,
                limit=args.limit,
                priority=args.priority,
                dry_run=not args.execute,
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        if summary["dry_run"] and summary["candidate_count"]:
            print(
                "Dry-run only. Re-run with --execute to enqueue normalize_report tasks.",
                file=sys.stderr,
            )
    finally:
        await pool.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect parsed Report rows missing canonical source_documents, "
            "or enqueue normalize_report tasks for them. Defaults to dry-run."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Enqueue normalize_report tasks for matched candidates.",
    )
    parser.add_argument(
        "--stock-code",
        default=None,
        help="Limit candidates to one stock ticker.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of candidates to inspect or enqueue.",
    )
    parser.add_argument(
        "--priority",
        choices=("batch", "immediate"),
        default="batch",
        help="Priority for enqueued normalize_report tasks.",
    )
    return parser


def main() -> None:
    asyncio.run(_run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
