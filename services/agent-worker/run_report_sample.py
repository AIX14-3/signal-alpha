"""증권사 리포트 샘플 수집·적재 원샷 러너 (FastAPI 서버 불필요).

지정한 종목들에 대해 ``COLLECT_REPORT`` 를 인큐하고, **REPORT 체인만**
(collect → process(PDF/local) → normalize → analyze) 인프로세스로 드레인한다.
끝단 집계/종합/발행(AGGREGATE/SYNTHESIZE/PUBLISH)은 의도적으로 제외해, 샘플 적재가
다른 소스 신호 발행을 건드리지 않게 한다(필요 시 워커 드레인 데몬이 끝단까지 처리).

기존 헬퍼/핸들러를 그대로 재사용한다(run_collectors.py 와 동일한 얇은 러너 관례):
  - mvp_runtime.bootstrap/load_env/build_pool/resolve_targets
  - app.orchestrator.queue.handlers.build_task_handlers
  - app.orchestrator.queue.tasks.QueueTaskRunner / QueueCycleRunner

사용법:
  uv run python run_report_sample.py                      # 기본 3종목, 최근 30일
  uv run python run_report_sample.py --ticker 005930      # 단일 종목
  uv run python run_report_sample.py --days-back 60
  uv run python run_report_sample.py --date-start 2025-07-01 --date-end 2025-09-30
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
logger = logging.getLogger("report_sample")

# 샘플 기본 대상 — 삼성전자 / SK하이닉스 / NAVER.
DEFAULT_TICKERS = ["005930", "000660", "035420"]


def _report_chain_plan() -> dict[str, int]:
    """REPORT 체인 task_type → 한 사이클 처리 캡. 끝단(집계/종합/발행)은 의도적으로 제외."""
    from app.orchestrator.queue.task_types import (
        ANALYZE_REPORT,
        COLLECT_REPORT,
        NORMALIZE_REPORT,
        PROCESS_REPORT,
    )

    return {
        COLLECT_REPORT: 50,
        PROCESS_REPORT: 1000,
        NORMALIZE_REPORT: 1000,
        ANALYZE_REPORT: 1000,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="증권사 리포트 샘플 수집·적재 원샷 러너")
    parser.add_argument("--ticker", action="append", help="대상 ticker(복수 지정 가능). 미지정 시 기본 3종목.")
    parser.add_argument("--days-back", type=int, default=30, help="조회 일수(date-start/end 미지정 시). 기본 30.")
    parser.add_argument("--date-start", help="YYYY-MM-DD. 지정 시 days-back 보다 우선.")
    parser.add_argument("--date-end", help="YYYY-MM-DD. 지정 시 days-back 보다 우선.")
    parser.add_argument("--max-pages", type=int, default=20, help="종목당 크롤링 최대 페이지. 기본 20.")
    args = parser.parse_args()

    load_env()
    from app.orchestrator.queue.handlers import build_task_handlers
    from app.orchestrator.queue.tasks import QueueCycleRunner, QueueTaskRunner
    from app.orchestrator.queue.task_types import COLLECT_REPORT
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    tickers = args.ticker or DEFAULT_TICKERS
    plan = _report_chain_plan()

    pool = await build_pool(max_pool_size=6)
    try:
        # 1) 대상 해석 + COLLECT_REPORT 인큐
        async with pool.acquire() as conn:
            targets = await resolve_targets(conn, tickers=tickers, limit=None)
            found = {t["ticker"] for t in targets}
            missing = [t for t in tickers if t not in found]
            if missing:
                logger.warning("stocks 에 없는 ticker(제외): %s", missing)
            if not targets:
                raise SystemExit("대상 종목이 없습니다. 먼저 stocks 시드를 적용하세요.")

            queue = ProcessingQueueRepository(conn)
            for target in targets:
                task_context: dict[str, object] = {
                    "stock_code": str(target["ticker"]).strip(),
                    "days_back": args.days_back,
                    "max_pages": args.max_pages,
                }
                if args.date_start:
                    task_context["date_start"] = args.date_start
                if args.date_end:
                    task_context["date_end"] = args.date_end
                task_id = await queue.enqueue(
                    stock_id=int(target["stock_id"]),
                    task_type=COLLECT_REPORT,
                    priority="batch",
                    task_context=task_context,
                    dedupe=True,
                )
                logger.info("인큐 COLLECT_REPORT %s (%s) → task#%s", target["ticker"], target["name"], task_id)

        # 2) REPORT 체인 드레인 — 진전이 없을 때까지 사이클 반복
        totals: dict[str, int] = {}
        while True:
            async with pool.acquire() as conn:
                handlers = build_task_handlers(conn)
                runner = QueueTaskRunner(conn, handlers)
                cycle = QueueCycleRunner(runner)
                result = await cycle.run_cycle(plan, max_passes=100000)
            for task_type, count in result.get("counts", {}).items():
                totals[task_type] = totals.get(task_type, 0) + count
            if result.get("failures"):
                logger.warning("사이클 실패 건수: %s", result["failures"])
            if result.get("total_runs", 0) == 0:
                break

        logger.info("드레인 완료 — 단계별 처리 건수: %s", totals or "(없음)")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
