"""Drive the Alternative (hiring/patent/datalab) analysis via the queue (#117).

No stock ids, tickers, weights, or windows are hardcoded: targets come from the
database (stocks that have hiring/patent/datalab raw rows), and every threshold
comes from env via the analyzer config dataclasses.

Queue-based flow:
  ① discover target stocks (fetch_target_stocks) and enqueue one deduped
     ANALYZE_ALTERNATIVE task per stock (as_of = today, YYYY-MM-DD).
  ② drain the queue with QueueTaskRunner across ``batch_concurrency`` workers;
     each task runs AlternativeAnalyzeTaskHandler (the same loaders/analyzers/
     aggregator/persistence as before — collectors now feed the same path).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import asyncpg  # type: ignore[import]

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "data-access"))

from app.aggregator import AlternativeAggregator
from app.analyzers.config import AggregatorConfig, AnalyzerRuntimeConfig
from app.analyzers.registry import build_registry
from app.orchestrator.alternative.tasks import AlternativeAnalyzeTaskHandler
from app.orchestrator.queue.task_types import ANALYZE_ALTERNATIVE
from app.orchestrator.queue.tasks import QueueTaskRunner

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


def _since(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value.replace("/", "-"))


async def fetch_target_stocks(
    conn: asyncpg.Connection,
    *,
    ticker: str | None,
    wanted: set[str],
    since: date | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    conditions = []
    if "HIRING" in wanted:
        conditions.append(
            "EXISTS (SELECT 1 FROM hiring_raw_details h "
            "JOIN raw_documents r ON r.id = h.raw_document_id "
            "WHERE h.stock_id = s.id AND ($2::date IS NULL OR r.published_at >= $2))"
        )
    if "PATENT" in wanted:
        conditions.append(
            "EXISTS (SELECT 1 FROM patent_raw_details p "
            "WHERE p.stock_id = s.id AND ($2::date IS NULL OR p.application_date >= $2))"
        )
    if "DATALAB" in wanted:
        conditions.append(
            "EXISTS (SELECT 1 FROM datalab_category_stocks dcs "
            "JOIN datalab_raw_details d ON d.category_id = dcs.category_id "
            "WHERE dcs.stock_id = s.id AND ($2::date IS NULL OR d.observed_date >= $2))"
        )
    if not conditions:
        return []

    query = f"""
        SELECT DISTINCT s.id AS stock_id, s.ticker, s.name
        FROM stocks s
        WHERE s.is_active = TRUE
          AND ($1::text IS NULL OR s.ticker = $1)
          AND ({' OR '.join(conditions)})
        ORDER BY s.id
        {'LIMIT $3' if limit else ''}
    """
    params: list[Any] = [ticker, since]
    if limit:
        params.append(limit)
    rows = await conn.fetch(query, *params)
    return [dict(row) for row in rows]


async def run_once(args: argparse.Namespace) -> None:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required.")

    runtime = AnalyzerRuntimeConfig.from_env()
    only_flags = {
        "HIRING": args.hiring_only,
        "PATENT": args.patent_only,
        "DATALAB": args.datalab_only,
    }
    wanted = (
        {source for source, on in only_flags.items() if on}
        if any(only_flags.values())
        else {"HIRING", "PATENT", "DATALAB"}
    )
    registrations = [r for r in build_registry() if r.source in wanted]
    if not registrations:
        raise RuntimeError("No source selected (check --hiring-only/--patent-only/--datalab-only).")

    # Queue-based: this driver enqueues one deduped ANALYZE_ALTERNATIVE per target
    # stock, then drains the queue with QueueTaskRunner. The analysis itself lives
    # in AlternativeAnalyzeTaskHandler (same loaders/analyzers/aggregator as before),
    # so collectors and this driver feed the *same* queue path (#117).
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    aggregator = AlternativeAggregator(AggregatorConfig.from_env())

    pool_max = max(
        int(os.getenv("ANALYZER_DB_POOL_MAX", "0")) or 0,
        runtime.batch_concurrency * 2 + 2,
    )
    pool = await asyncpg.create_pool(
        **parse_dsn(dsn),
        min_size=1,
        max_size=pool_max,
        ssl="require",
        statement_cache_size=0,
    )
    as_of = date.today()
    as_of_str = as_of.isoformat()  # stable YYYY-MM-DD dedupe key
    summary = {"processed": 0, "published": 0, "no_data": 0, "error": 0}

    def _build_runner(conn: Any) -> QueueTaskRunner:
        handler = AlternativeAnalyzeTaskHandler(
            conn,
            registrations=registrations,
            aggregator=aggregator,
            runtime_config=runtime,
        )
        return QueueTaskRunner(conn, {ANALYZE_ALTERNATIVE: handler})

    try:
        async with pool.acquire() as conn:
            targets = await fetch_target_stocks(
                conn,
                ticker=args.ticker,
                wanted=wanted,
                since=_since(args.since),
                limit=args.limit,
            )
            queue_repository = ProcessingQueueRepository(conn)
            for target in targets:
                await queue_repository.enqueue(
                    stock_id=int(target["stock_id"]),
                    task_type=ANALYZE_ALTERNATIVE,
                    priority="batch",
                    task_context={"as_of": as_of_str, "stock_code": target["ticker"]},
                    dedupe=True,
                )

        print("\nALTERNATIVE ANALYZER")
        print("=" * 60)
        print(f"targets={len(targets)} sources={sorted(wanted)} concurrency={runtime.batch_concurrency}")

        async def drain_worker() -> None:
            while True:
                async with pool.acquire() as conn:
                    result = await _build_runner(conn).run_task(ANALYZE_ALTERNATIVE)
                status = result["status"]
                if status == "idle":
                    return
                if status == "success":
                    payload = result.get("result") or {}
                    summary["processed"] += 1
                    if payload.get("available_sources"):
                        summary["published"] += 1
                    else:
                        summary["no_data"] += 1
                    print(
                        f"  stock_id={payload.get('stock_id')}: {payload.get('direction')} "
                        f"{float(payload.get('score') or 0.0):+.3f} "
                        f"avail={payload.get('available_sources')} final={payload.get('final_signal_id')}"
                    )
                else:  # failed/skipped — isolate and keep draining
                    summary["error"] += 1
                    print(f"  task_id={result.get('task_id')}: {status} {result.get('error', '')}")

        await asyncio.gather(*[drain_worker() for _ in range(max(1, runtime.batch_concurrency))])

        print("-" * 60)
        print(f"SUMMARY {summary}")
        attempted = summary["processed"] + summary["error"]
        success_rate = (summary["processed"] / attempted * 100) if attempted else 0.0
        print(
            f"📊 분석 성공률 {success_rate:.1f}% "
            f"(성공 {summary['processed']} / 시도 {attempted} · "
            f"신호발행 {summary['published']} · 데이터없음 {summary['no_data']} · 오류 {summary['error']})"
        )
    finally:
        await pool.close()


async def main() -> None:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", help="Limit analysis to one ticker.")
    parser.add_argument("--since", help="YYYY-MM-DD lower bound for target discovery.")
    parser.add_argument("--limit", type=int, help="Cap the number of stocks processed.")
    parser.add_argument("--hiring-only", action="store_true")
    parser.add_argument("--patent-only", action="store_true")
    parser.add_argument("--datalab-only", action="store_true")
    parser.add_argument("--loop", action="store_true", help="Repeat until interrupted.")
    parser.add_argument("--interval-seconds", type=int, default=3600)
    args = parser.parse_args()

    while True:
        await run_once(args)
        if not args.loop:
            break
        await asyncio.sleep(args.interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
