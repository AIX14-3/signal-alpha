"""Dry-run or skip legacy processing_queue tasks after queue contract changes."""

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
    from app.orchestrator.queue.legacy_cleanup import cleanup_legacy_dart_backfill_tasks

    pool = await build_pool(max_pool_size=2)
    try:
        async with pool.acquire() as connection:
            summary = await cleanup_legacy_dart_backfill_tasks(
                connection,
                execute=args.execute,
                limit=args.limit,
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        if summary["dry_run"] and summary["matched_count"]:
            print(
                "Dry-run only. Re-run with --execute to mark matched legacy tasks skipped.",
                file=sys.stderr,
            )
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect or skip removed legacy processing_queue task types. "
            "Defaults to dry-run."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Mark matched legacy active tasks as skipped.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximum number of matched tasks to skip in one execution.",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
