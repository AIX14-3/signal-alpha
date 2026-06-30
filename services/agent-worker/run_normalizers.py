"""Drain the alternative-source NORMALIZE queue (patent/datalab/hiring).

Collectors enqueue ``NORMALIZE_*`` tasks (raw_documents → source_documents/
signal_events). The agent-worker lifespan does **not** auto-drain the queue (only
the price/ops daemons run), and ``run_analyzers.py`` drains **only** the
``ANALYZE_{SOURCE}`` stages. So between "collect" and "analyze" the normalize step has
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

# Windows 콘솔(cp949)에서 ⚠️ 등 비-ASCII 기호 print 시 UnicodeEncodeError 방지.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

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


async def _drain_source(pool: asyncpg.Pool, source: str, concurrency: int) -> dict[str, int]:
    """Drain one source's NORMALIZE queue with ``concurrency`` parallel workers.

    안전하게 병렬화 가능: ``claim_next_pending`` 이 ``FOR UPDATE SKIP LOCKED`` 라
    워커들이 같은 태스크를 잡지 않는다. 각 워커는 자기 커넥션으로 claim→처리 루프를
    돌며 큐가 idle(=잡을 게 없음)을 반환할 때까지 비운다. 직렬 1건씩이던 기존 구조는
    대량 백로그(예: 특허 정규화 ~15만건)를 90분 워크플로 안에 못 비웠다 → N배 처치량.
    """
    task_type, handler_factory = _SOURCES[source]

    async def _worker() -> dict[str, int]:
        counts = {"success": 0, "skipped": 0, "error": 0}
        for _ in range(_MAX_TASKS_PER_SOURCE):
            async with pool.acquire() as conn:
                runner = QueueTaskRunner(conn, {task_type: handler_factory(conn)})
                result = await runner.run_task(task_type)
            status = result["status"]
            if status == "idle":  # SKIP LOCKED: 잡을 pending 이 없음(다른 워커가 처리 중 포함)
                break
            if status == "success":
                counts["success"] += 1
            elif status == "skipped":
                counts["skipped"] += 1
            else:  # failed — isolate and keep draining
                counts["error"] += 1
                print(f"  {task_type} task_id={result.get('task_id')}: {status} {result.get('error', '')}")
        else:
            print(f"  WARN {task_type}: hit per-worker cap {_MAX_TASKS_PER_SOURCE} — stopping (possible re-claim loop)")
        return counts

    totals = {"success": 0, "skipped": 0, "error": 0}
    for partial in await asyncio.gather(*(_worker() for _ in range(max(1, concurrency)))):
        for key in totals:
            totals[key] += partial[key]
    return totals


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

    # 병렬 드레인 워커 수. 기본 8(env NORMALIZE_CONCURRENCY 또는 --concurrency 로 조정).
    concurrency = max(1, int(args.concurrency or os.getenv("NORMALIZE_CONCURRENCY", "8")))

    params = parse_dsn(dsn)
    pool = await asyncpg.create_pool(
        **params,
        min_size=1,
        max_size=max(4, concurrency + 1),  # 워커당 커넥션 1개 + 여유
        ssl=resolve_ssl(params["host"]),
        statement_cache_size=0,
    )
    try:
        print("\nALTERNATIVE NORMALIZER")
        print("=" * 60)
        print(f"draining sources={sorted(wanted)} (concurrency={concurrency})")
        totals = {"success": 0, "skipped": 0, "error": 0}
        for source in sorted(wanted):
            counts = await _drain_source(pool, source, concurrency)
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
    parser.add_argument(
        "--concurrency", type=int, default=0,
        help="Parallel drain workers per source (default: env NORMALIZE_CONCURRENCY or 8).",
    )
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
