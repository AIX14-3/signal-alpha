"""One-shot or watch-mode worker queue drain helper.

This uses the same bounded fair cycle as the queue drain daemon and
`POST /internal/queue/run-cycle`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker

from mvp_runtime import bootstrap, build_pool, load_env

bootstrap()


async def _run(args: argparse.Namespace) -> None:
    load_env()
    from app.orchestrator.queue.drain_daemon import run_drain_cycle

    pool = await build_pool(max_pool_size=max(2, args.pool_size))
    try:
        cycle = 0
        while True:
            cycle += 1
            summary = await run_drain_cycle(pool)
            total = int(summary.get("total_runs") or 0)
            if total:
                print(f"[cycle {cycle}] drained {total}: {summary}")
            else:
                print(f"[cycle {cycle}] idle: {summary}")
            if not args.watch:
                statuses = summary.get("statuses") or {}
                bad = int(statuses.get("failed") or 0) + int(statuses.get("skipped") or 0)
                if bad:
                    raise SystemExit(f"queue drain saw {bad} failed/skipped task(s); check logs.")
                return
            await asyncio.sleep(args.interval_seconds)
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run bounded fair processing_queue drain cycles."
    )
    parser.add_argument("--watch", action="store_true", help="Continue running drain cycles.")
    parser.add_argument("--interval-seconds", type=float, default=5.0, help="Watch interval.")
    parser.add_argument("--pool-size", type=int, default=8, help="Maximum DB pool size.")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
