"""Drain the alternative-source NORMALIZE queue (patent/datalab/hiring).

Collectors enqueue ``NORMALIZE_*`` tasks (raw_documents → source_documents/
signal_events). The agent-worker lifespan does **not** auto-drain the queue (only
the price/ops daemons run), and ``run_analyzers.py`` drains **only**
``ANALYZE_ALTERNATIVE``. So between "collect" and "analyze" the normalize step has
no driver. This CLI fills that gap so the scheduled pipeline
(collect → normalize → analyze) runs end to end on a runner that attaches directly
to the DB — no publicly reachable worker required.

  uv run python run_normalizers.py                 # drain patent + datalab + hiring
  uv run python run_normalizers.py --patent-only    # one source

Mirrors ``run_analyzers.py``'s DSN/pool/drain pattern; registers only the
alternative-data normalize handlers (no DART/report/ML — those are other teams).
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

from app.orchestrator.alternative.tasks import (
    DataLabNormalizeTaskHandler,
    HiringNormalizeTaskHandler,
    PatentNormalizeTaskHandler,
)
from app.orchestrator.queue.task_types import (
    NORMALIZE_DATALAB,
    NORMALIZE_HIRING,
    NORMALIZE_PATENT,
)
from app.orchestrator.queue.tasks import QueueTaskRunner

ROOT = Path(__file__).resolve().parents[2]

# source → (task_type, handler factory). Each handler takes a single connection,
# matching build_task_handlers (app/orchestrator/queue/handlers.py).
_SOURCES: dict[str, tuple[str, Any]] = {
    "PATENT": (NORMALIZE_PATENT, PatentNormalizeTaskHandler),
    "DATALAB": (NORMALIZE_DATALAB, DataLabNormalizeTaskHandler),
    "HIRING": (NORMALIZE_HIRING, HiringNormalizeTaskHandler),
}

# Safety cap so a task that keeps re-claiming can never spin forever.
_MAX_TASKS_PER_SOURCE = 100_000


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


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}


def resolve_ssl(host: str) -> Any:
    """SSL mode for asyncpg: managed Postgres needs 'require'; a local Docker
    Postgres rejects SSL. Default by host; ``DB_SSL`` env overrides."""
    override = os.getenv("DB_SSL")
    if override:
        return False if override.lower() in {"disable", "off", "false", "0", "no"} else override
    return False if host in _LOCAL_HOSTS else "require"


async def _drain_source(pool: asyncpg.Pool, source: str) -> dict[str, int]:
    task_type, handler_factory = _SOURCES[source]
    counts = {"success": 0, "skipped": 0, "error": 0}
    for _ in range(_MAX_TASKS_PER_SOURCE):
        async with pool.acquire() as conn:
            runner = QueueTaskRunner(conn, {task_type: handler_factory(conn)})
            result = await runner.run_task(task_type)
        status = result["status"]
        if status == "idle":
            break
        if status == "success":
            counts["success"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        else:  # failed — isolate and keep draining
            counts["error"] += 1
            print(f"  {task_type} task_id={result.get('task_id')}: {status} {result.get('error', '')}")
    else:
        print(f"  ⚠️ {task_type}: hit {_MAX_TASKS_PER_SOURCE} task cap — stopping (possible re-claim loop)")
    return counts


async def run_once(args: argparse.Namespace) -> None:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required.")

    only_flags = {
        "PATENT": args.patent_only,
        "DATALAB": args.datalab_only,
        "HIRING": args.hiring_only,
    }
    wanted = {s for s, on in only_flags.items() if on} or set(_SOURCES)

    params = parse_dsn(dsn)
    pool = await asyncpg.create_pool(
        **params,
        min_size=1,
        max_size=4,
        ssl=resolve_ssl(params["host"]),
        statement_cache_size=0,
    )
    try:
        print("\nALTERNATIVE NORMALIZER")
        print("=" * 60)
        print(f"draining sources={sorted(wanted)}")
        totals = {"success": 0, "skipped": 0, "error": 0}
        for source in sorted(wanted):
            counts = await _drain_source(pool, source)
            print(f"  {source}: {counts}")
            for key in totals:
                totals[key] += counts[key]
        print("-" * 60)
        print(f"SUMMARY {totals}")
    finally:
        await pool.close()


async def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Drain alternative-source NORMALIZE queue.")
    parser.add_argument("--patent-only", action="store_true")
    parser.add_argument("--datalab-only", action="store_true")
    parser.add_argument("--hiring-only", action="store_true")
    parser.add_argument("--loop", action="store_true", help="Repeat draining until interrupted.")
    parser.add_argument("--interval-seconds", type=int, default=3600)
    args = parser.parse_args()

    while True:
        await run_once(args)
        if not args.loop:
            break
        await asyncio.sleep(args.interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
