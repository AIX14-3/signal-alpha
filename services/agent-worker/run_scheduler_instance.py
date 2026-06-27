"""스케줄러 인스턴스 엔트리포인트 — 수집 자동화 + 분석 팬아웃 트리거 (#11 7영역 분리).

기존에 `/internal/schedules/*` 엔드포인트(상주 호출자 없음)와 `enqueue_stock_pipeline`(수동)만
있어, 주기적으로 큐를 채워주는 주체가 없었다. 이 스케줄러 유닛이 주기마다:

  1. 수집 스케줄 인큐 — DartCollectionScheduler / ReportCollectionScheduler 로 활성 종목의
     COLLECT_DART / COLLECT_REPORT 를 인큐(dedupe).
  2. 분석 팬아웃 — 활성 종목별 enqueue_stock_pipeline 으로 ANALYZE_PRICE / ANALYZE_ALTERNATIVE /
     AGGREGATE_SIGNAL 을 인큐(dedupe).

이렇게 채운 큐는 **워커 드레인 데몬**(QUEUE_DRAIN_DAEMON_ENABLED)이 끝단(발행)까지 소비한다.
스케줄러/워커/수집기는 서로 다른 인스턴스다 — 이 프로세스는 인큐만 하고 핸들러를 돌리지 않는다.
스케줄러 자신은 리포지토리로 큐에 직접 인큐하므로 HTTP/별도 의존성이 필요 없다.

  uv run python run_scheduler_instance.py                 # 1시간 주기 루프
  uv run python run_scheduler_instance.py --once          # 1회만
  uv run python run_scheduler_instance.py --interval-seconds 1800
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker

from mvp_runtime import bootstrap, build_pool, load_env, resolve_targets

bootstrap()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scheduler_instance")


async def run_cycle(pool: Any, *, fanout_limit: int, days_back: int) -> dict[str, int]:
    """스케줄러 1주기: 수집 스케줄 인큐 + 활성 종목 분석 팬아웃. 인큐 건수 요약을 반환."""
    from signal_alpha_data_access.repositories import ProcessingQueueRepository, StockRepository

    from app.orchestrator.dart.scheduler import DartCollectionScheduler
    from app.orchestrator.full_pipeline import enqueue_stock_pipeline
    from app.orchestrator.report.scheduler import ReportCollectionScheduler

    summary = {"dart_collect": 0, "report_collect": 0, "pipeline_stocks": 0}
    signal_date = date.today()

    async with pool.acquire() as conn:
        stock_repo = StockRepository(conn)
        queue = ProcessingQueueRepository(conn)

        # 1) 수집 스케줄 — 활성 종목의 COLLECT_DART / COLLECT_REPORT 인큐(dedupe).
        dart = await DartCollectionScheduler(
            stock_repository=stock_repo, queue_repository=queue
        ).enqueue_due_collections(limit=fanout_limit, priority="batch")
        summary["dart_collect"] = int(dart.get("scheduled_count") or 0)

        report = await ReportCollectionScheduler(
            stock_repository=stock_repo, queue_repository=queue
        ).enqueue_due_collections(limit=fanout_limit, days_back=days_back, priority="batch")
        summary["report_collect"] = int(report.get("scheduled_count") or 0)

        # 2) 분석 팬아웃 — 활성 종목별 PRICE/ALTERNATIVE/AGGREGATE 인큐(dedupe).
        #    DART 수집은 위 스케줄러가 자체 윈도우로 담당하므로 여기선 include_dart_collection=False.
        targets = await resolve_targets(conn, tickers=None, limit=fanout_limit)
        for target in targets:
            await enqueue_stock_pipeline(
                queue,
                stock_id=int(target["stock_id"]),
                stock_code=str(target["ticker"]),
                signal_date=signal_date,
                priority="batch",
            )
        summary["pipeline_stocks"] = len(targets)

    logger.info("스케줄러 주기 완료: %s", summary)
    return summary


async def main() -> None:
    parser = argparse.ArgumentParser(description="Signal α 스케줄러 — 수집/분석 큐를 주기적으로 채운다.")
    parser.add_argument("--once", action="store_true", help="1회만 실행(루프 없이).")
    parser.add_argument("--interval-seconds", type=int, default=3600, help="주기(초). 기본 3600.")
    parser.add_argument("--limit", type=int, default=100, help="주기당 처리할 활성 종목 상한.")
    parser.add_argument("--days-back", type=int, default=7, help="리포트 수집 조회 일수.")
    args = parser.parse_args()

    load_env()
    pool = await build_pool(max_pool_size=4)
    try:
        while True:
            try:
                await run_cycle(pool, fanout_limit=args.limit, days_back=args.days_back)
            except Exception:  # noqa: BLE001 - 한 주기 실패가 스케줄러를 죽이지 않게
                logger.exception("스케줄러 주기 실패 — 다음 주기에 재시도")
            if args.once:
                break
            await asyncio.sleep(args.interval_seconds)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
