"""현재(최신일) 신호만 집계→종합(LLM)→발행하는 원샷 러너.

증권사 리포트 적재 후, **최종 리포트에 결과가 보이도록** 종목별 *최신 REPORT 분석일*에 대해서만
AGGREGATE_SIGNAL 을 직접 실행하고(과거 날짜의 대기 집계 태스크는 건드리지 않음), 그로 인해
인큐된 SYNTHESIZE→PUBLISH_SIGNALS 만 드레인한다. 과거 2년 전체를 발행하면 LLM 종합이 날짜
수만큼(수십~수백 회) 호출되므로, 현재 신호만 좁혀 비용을 통제한다.

전체 백필이 필요하면 워커 드레인 데몬(QUEUE_DRAIN_DAEMON_ENABLED=true) 또는 전 task_type 드레인을
쓰면 대기 중인 과거 AGGREGATE_SIGNAL 까지 끝단으로 흘러간다.

사용법:
  uv run python run_publish_current.py                 # 기본 3종목 최신일
  uv run python run_publish_current.py --ticker 005930
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker

from mvp_runtime import bootstrap, build_pool, load_env, resolve_targets

bootstrap()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("publish_current")

DEFAULT_TICKERS = ["005930", "000660", "035420"]


async def main() -> None:
    parser = argparse.ArgumentParser(description="현재 신호 집계→종합→발행 원샷 러너")
    parser.add_argument("--ticker", action="append", help="대상 ticker(복수 지정 가능). 미지정 시 기본 3종목.")
    args = parser.parse_args()

    load_env()
    from app.orchestrator.queue.handlers import build_task_handlers
    from app.orchestrator.queue.tasks import QueueCycleRunner, QueueTaskRunner
    from app.orchestrator.queue.task_types import (
        AGGREGATE_SIGNAL,
        PUBLISH_SIGNALS,
        SYNTHESIZE,
    )

    tickers = args.ticker or DEFAULT_TICKERS
    pool = await build_pool(max_pool_size=6)
    try:
        # 1) 종목별 최신 REPORT 분석일에 대해 AGGREGATE_SIGNAL 직접 실행
        async with pool.acquire() as conn:
            targets = await resolve_targets(conn, tickers=tickers, limit=None)
            handlers = build_task_handlers(conn)
            aggregate_handler = handlers[AGGREGATE_SIGNAL]
            for target in targets:
                stock_id = int(target["stock_id"])
                latest = await conn.fetchval(
                    "SELECT MAX(analysis_date) FROM analysis_results "
                    "WHERE stock_id=$1 AND run_key='REPORT'",
                    stock_id,
                )
                if latest is None:
                    logger.warning("REPORT 분석 결과 없음 — 건너뜀: %s", target["ticker"])
                    continue
                result = await aggregate_handler(
                    {
                        "stock_id": stock_id,
                        "task_context": {
                            "signal_date": latest.isoformat(),
                            "stock_code": str(target["ticker"]).strip(),
                            "priority": "batch",
                        },
                    }
                )
                logger.info(
                    "집계 %s @%s → final_signal#%s signal=%s score=%s synth_task=%s",
                    target["ticker"],
                    latest,
                    result.get("final_signal_id"),
                    result.get("signal"),
                    result.get("final_score"),
                    result.get("synthesize_task_id"),
                )

        # 2) 방금 인큐된 SYNTHESIZE→PUBLISH 만 드레인(과거 AGGREGATE_SIGNAL 은 미처리로 남김)
        plan = {SYNTHESIZE: 100, PUBLISH_SIGNALS: 100}
        totals: dict[str, int] = {}
        while True:
            async with pool.acquire() as conn:
                handlers = build_task_handlers(conn)
                runner = QueueTaskRunner(conn, handlers)
                cycle = QueueCycleRunner(runner)
                res = await cycle.run_cycle(plan, max_passes=10000)
            for task_type, count in res.get("counts", {}).items():
                totals[task_type] = totals.get(task_type, 0) + count
            if res.get("failures"):
                logger.warning("사이클 실패: %s", res["failures"])
            if res.get("total_runs", 0) == 0:
                break

        logger.info("종합·발행 완료 — 처리 건수: %s", totals or "(없음)")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
