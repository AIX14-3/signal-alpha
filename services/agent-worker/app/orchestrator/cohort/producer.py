"""SCORE_COHORT 프로듀서 — 대상 유니버스를 결정론적 코호트 청크로 잘라 인큐.

코호트 축: ``stocks.sector`` 는 nullable 이고 현 유니버스에서 세부 섹터가 대부분
싱글턴(27종목 ≈ 20섹터)이라 **그룹 키로 쓸 수 없다**. 대신 sector 를 soft 정렬
키로만 써서(같은 섹터가 같은 청크에 인접하도록) ticker 순으로 고정 크기 청크를
자른다 — 청크가 결정론적이면 같은 날 재시드돼도 dedupe 가 잡는다.

시드는 드레인 데몬이 ``_seed_episode_outcome_task`` 와 같은 결로 호출한다
(일 1회 가드: 열린 태스크 또는 최근 완료분이 있으면 재시드하지 않음).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.core.config import Settings
from app.orchestrator.queue.task_types import SCORE_COHORT
from signal_alpha_data_access.repositories import ProcessingQueueRepository

logger = logging.getLogger(__name__)

# 시드 최소 간격(초) — 일 1회.
SEED_INTERVAL_SEC = 86400.0


def chunk_universe(
    rows: list[tuple[str, str | None]], cohort_size: int
) -> list[list[str]]:
    """(ticker, sector) 목록 → 결정론적 코호트 청크. sector soft-정렬 후 고정 크기."""
    ordered = sorted(rows, key=lambda r: (r[1] is None, r[1] or "", r[0]))
    tickers = [t for t, _s in ordered]
    size = max(1, int(cohort_size))
    return [tickers[i : i + size] for i in range(0, len(tickers), size)]


async def seed_cohort_tasks(pool: Any, settings: Settings, *, as_of: date | None = None) -> int:
    """LLM 채점 대상 소스별로 SCORE_COHORT 태스크를 시드한다. 반환 = 인큐 건수."""
    if not settings.llm_scoring_enabled:
        return 0
    as_of = as_of or date.today()
    enqueued = 0
    async with pool.acquire() as conn:
        recent = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM processing_queue
                WHERE task_type = $1
                  AND (
                      status IN ('pending', 'running', 'retrying')
                      OR finished_at > NOW() - make_interval(secs => $2)
                  )
            )
            """,
            SCORE_COHORT,
            SEED_INTERVAL_SEC,
        )
        if recent:
            return 0
        rows = await conn.fetch(
            "SELECT ticker, sector FROM stocks WHERE is_target IS TRUE ORDER BY ticker"
        )
        universe = [(str(r["ticker"]), r["sector"]) for r in rows]
        if not universe:
            return 0
        chunks = chunk_universe(universe, settings.llm_cohort_size)
        queue = ProcessingQueueRepository(conn)
        for source in settings.llm_scoring_sources:
            for chunk in chunks:
                await queue.enqueue(
                    stock_id=None,  # 배치 태스크 (RECORD_EPISODE_OUTCOMES 선례)
                    task_type=SCORE_COHORT,
                    priority="batch",
                    task_context={
                        "source": source,
                        "as_of": as_of.isoformat(),
                        "tickers": chunk,
                    },
                    dedupe=True,
                )
                enqueued += 1
    if enqueued:
        logger.info(
            "seeded %d SCORE_COHORT tasks (%d sources × %d chunks)",
            enqueued,
            len(settings.llm_scoring_sources),
            len(chunks),
        )
    return enqueued
