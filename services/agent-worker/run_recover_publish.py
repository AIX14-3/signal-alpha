"""멀티소스 복구 발행 원샷 러너 (수집 DB 리셋 후 백엔드 재발행).

3종목에 대해 가용 소스(DART 재수집 + PRICE/REPORT/대체 분석)를 채운 뒤, **현재일** 집계를
직접 호출(과거 REPORT 집계 대기 163건은 건드리지 않음)하고 LLM 종합→백엔드 발행까지 흘린다.

선행: 백엔드 published 5테이블 리셋(드리프트 해소) 후 실행해야 발행이 충돌 없이 성공한다.
주의: PATENT/DATALAB 은 API 키 부재로 수집 불가 — 분석은 무데이터로 no_signal/missing 처리된다.

사용법:
  uv run python run_recover_publish.py
  uv run python run_recover_publish.py --ticker 005930
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mvp_runtime import bootstrap, build_pool, load_env, resolve_targets

bootstrap()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("recover_publish")

DEFAULT_TICKERS = ["005930", "000660", "035420"]


async def main() -> None:
    parser = argparse.ArgumentParser(description="멀티소스 복구 발행 원샷 러너")
    parser.add_argument("--ticker", action="append", help="대상 ticker(복수). 미지정 시 기본 3종목.")
    args = parser.parse_args()

    load_env()
    from app.orchestrator.full_pipeline import enqueue_stock_pipeline
    from app.orchestrator.queue.handlers import build_task_handlers
    from app.orchestrator.queue.tasks import QueueCycleRunner, QueueTaskRunner
    from app.orchestrator.queue.task_types import (
        AGGREGATE_SIGNAL,
        ANALYZE_DART,
        ANALYZE_DATALAB,
        ANALYZE_HIRING,
        ANALYZE_PATENT,
        ANALYZE_PRICE,
        COLLECT_DART,
        NORMALIZE_DART,
        PUBLISH_SIGNALS,
        RETURN_COMBINE,
        SRC_INFER,
        SYNTHESIZE,
    )
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    tickers = args.ticker or DEFAULT_TICKERS

    # 소스/분석 단계만 드레인(집계/종합/발행 제외 — 집계는 직접 현재일만 호출).
    analysis_plan = {
        COLLECT_DART: 50,
        NORMALIZE_DART: 1000,
        ANALYZE_DART: 1000,
        ANALYZE_PRICE: 50,
        ANALYZE_DATALAB: 50,
        ANALYZE_HIRING: 50,
        ANALYZE_PATENT: 50,
        SRC_INFER: 200,
        RETURN_COMBINE: 200,
    }
    publish_plan = {SYNTHESIZE: 100, PUBLISH_SIGNALS: 100}

    pool = await build_pool(max_pool_size=6)
    try:
        # 0) 대상 + 종목별 현재(최신 REPORT 분석일) 산출
        async with pool.acquire() as conn:
            targets = await resolve_targets(conn, tickers=tickers, limit=None)
            queue = ProcessingQueueRepository(conn)
            sig_dates: dict[int, object] = {}
            for t in targets:
                sid = int(t["stock_id"])
                latest = await conn.fetchval(
                    "SELECT MAX(analysis_date) FROM analysis_results WHERE stock_id=$1 AND run_key='REPORT'",
                    sid,
                )
                if latest is None:
                    latest = await conn.fetchval("SELECT CURRENT_DATE")
                sig_dates[sid] = latest
                # 소스 팬아웃 인큐(price+alt+dart). 여기서 만든 aggregate 는 드레인하지 않고 직접 호출로 대체.
                ids = await enqueue_stock_pipeline(
                    queue,
                    stock_id=sid,
                    stock_code=str(t["ticker"]).strip(),
                    signal_date=latest,
                    include_dart_collection=True,
                )
                logger.info("팬아웃 인큐 %s @%s: %s", t["ticker"], latest, ids)

        # 1) 소스/분석 드레인
        await _drain(pool, analysis_plan, build_task_handlers, QueueTaskRunner, QueueCycleRunner, label="분석")

        # 2) 현재일 집계 직접 호출(과거 대기 집계 미처리) → SYNTHESIZE 인큐
        async with pool.acquire() as conn:
            handlers = build_task_handlers(conn)
            aggregate = handlers[AGGREGATE_SIGNAL]
            for t in targets:
                sid = int(t["stock_id"])
                res = await aggregate({
                    "stock_id": sid,
                    "task_context": {
                        "signal_date": sig_dates[sid].isoformat(),
                        "stock_code": str(t["ticker"]).strip(),
                        "priority": "batch",
                    },
                })
                logger.info(
                    "집계 %s @%s → final_signal#%s signal=%s score=%s aggregated=%s synth=%s",
                    t["ticker"], sig_dates[sid], res.get("final_signal_id"), res.get("signal"),
                    res.get("final_score"), res.get("aggregated_count"), res.get("synthesize_task_id"),
                )

        # 3) 종합(LLM)→발행 드레인
        await _drain(pool, publish_plan, build_task_handlers, QueueTaskRunner, QueueCycleRunner, label="종합·발행")
    finally:
        await pool.close()


async def _drain(pool, plan, build_task_handlers, QueueTaskRunner, QueueCycleRunner, *, label):
    totals: dict[str, int] = {}
    while True:
        async with pool.acquire() as conn:
            handlers = build_task_handlers(conn)
            runner = QueueTaskRunner(conn, handlers)
            cycle = QueueCycleRunner(runner)
            res = await cycle.run_cycle(plan, max_passes=100000)
        for tt, c in res.get("counts", {}).items():
            totals[tt] = totals.get(tt, 0) + c
        if res.get("failures"):
            logger.warning("[%s] 사이클 실패: %s", label, res["failures"])
        if res.get("total_runs", 0) == 0:
            break
    logger.info("[%s] 드레인 완료 — %s", label, totals or "(없음)")


if __name__ == "__main__":
    asyncio.run(main())
