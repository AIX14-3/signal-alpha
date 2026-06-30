"""범용 큐 드레인 데몬 — processing_queue 를 끝단까지 연속 소비 (#11 워커 영역 완성).

기존 ``run_pipeline._drain`` 의 "idle 될 때까지 순회" 로직을 **전 task_type** 으로 확장해
상주 데몬화한다. 한 워커 프로세스에서 수집→정규화→분석→집계→게이트→종합→**발행
(PUBLISH_SIGNALS)** 까지 큐가 끝단으로 흐른다. 이전에는 큐를 연속 소비하는 주체가 없어
(`run_pipeline` 은 MVP 5종·PUBLISH 제외, HTTP run-batch 는 수동) 발행이 멈춰 있었다.

ops/price 데몬과 **동일 패턴**: advisory lock 으로 단일 기동을 보장하고(중복 드레인 방지),
한 틱 실패가 데몬을 죽이지 않으며, 예기치 못한 죽음이면 supervisor 가 재기동한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from app.core.config import Settings
from app.orchestrator.queue.handlers import build_task_handlers
from app.orchestrator.queue.tasks import QueueTaskRunner
from app.orchestrator.queue.task_types import (
    AGGREGATE_SIGNAL,
    ANALYZE_DART,
    ANALYZE_DATALAB,
    ANALYZE_HIRING,
    ANALYZE_PATENT,
    ANALYZE_PRICE,
    ANALYZE_REPORT,
    COLLECT_DART,
    COLLECT_REPORT,
    ENRICH_HIRING,
    ENRICH_PATENT,
    NORMALIZE_DART,
    NORMALIZE_DATALAB,
    NORMALIZE_HIRING,
    NORMALIZE_PATENT,
    NORMALIZE_REPORT,
    PROCESS_REPORT,
    PUBLISH_SIGNALS,
    RETURN_COMBINE,
    SRC_INFER,
    SYNTHESIZE,
)

logger = logging.getLogger("queue_drain_daemon")

# price(0x50524943 "PRIC")·ops(0x4F505348 "OPSH") 와 겹치지 않는 키 — "QDRN".
_DRAIN_ADVISORY_LOCK_KEY = 0x5144524E
_RESTART_DELAY_SEC = 60.0

# 체인 의존(FK) 순서 — 드레인 효율용. 한 패스에서 상류 단계가 만든 태스크를 같은 패스 하류에서
# 바로 소비하도록 정렬한다. 정확성(끝단 도달)은 ``drain_until_idle`` 의 progressed 루프가 보장하므로
# 이 순서가 어긋나도 결과는 같고, 맞으면 패스 수만 줄어든다.
DRAIN_ORDER: tuple[str, ...] = (
    # 수집 → 정규화 → 분석 (소스별)
    COLLECT_DART,
    NORMALIZE_DART,
    ANALYZE_DART,
    COLLECT_REPORT,
    PROCESS_REPORT,
    NORMALIZE_REPORT,
    ANALYZE_REPORT,
    NORMALIZE_HIRING,
    ENRICH_HIRING,
    NORMALIZE_PATENT,
    ENRICH_PATENT,
    NORMALIZE_DATALAB,
    # 대체데이터 소스별 분석 스테이지 (C안 Phase 3 — 1태스크=1소스)
    ANALYZE_DATALAB,
    ANALYZE_HIRING,
    ANALYZE_PATENT,
    ANALYZE_PRICE,
    # 메타러너 예측 라인 (주가 BASE 앵커 + 소스 return 채널)
    SRC_INFER,
    RETURN_COMBINE,
    # 집계 → 종합 → 발행 (발행 차단 게이트 폐기 — 종합 결과를 곧장 발행)
    AGGREGATE_SIGNAL,
    SYNTHESIZE,
    PUBLISH_SIGNALS,
)


def _resolve_order(handler_keys: Any) -> list[str]:
    """DRAIN_ORDER 를 우선하되, 핸들러에 등록됐지만 순서에 없는 task_type 을 뒤에 덧붙인다.
    (새 task_type 추가 시 드레인에서 누락되지 않게 하는 안전장치.)"""
    known = set(handler_keys)
    ordered = [t for t in DRAIN_ORDER if t in known]
    extra = [t for t in handler_keys if t not in set(DRAIN_ORDER)]
    return ordered + extra


HandlerFactory = Callable[[Any], dict[str, Any]]


async def drain_until_idle(
    pool: Any, *, handler_factory: HandlerFactory = build_task_handlers
) -> dict[str, int]:
    """전 task_type 을 의존 순서로 순회하며, 한 패스가 전부 idle 일 때까지 반복 드레인.

    한 패스마다 풀에서 연결 1개를 잡아 그 위에서 핸들러를 구성(연결 바인딩)하고 모든
    task_type 을 1회씩 claim→실행한다. 상류가 만든 태스크를 하류가 같은/다음 패스에서
    소비하므로, 진척이 없을 때(=큐가 비었거나 더 진행 불가) 종료한다. 상태별 건수를 반환.

    ``handler_factory`` 는 주입 가능(테스트는 DB 없이 fake 핸들러로 검증).
    """
    counts: dict[str, int] = {}
    while True:
        progressed = False
        async with pool.acquire() as conn:
            handlers = handler_factory(conn)
            runner = QueueTaskRunner(conn, handlers)
            for task_type in _resolve_order(handlers.keys()):
                result = await runner.run_task(task_type)
                status = result["status"]
                if status == "idle":
                    continue
                progressed = True
                counts[status] = counts.get(status, 0) + 1
                if status in {"failed", "skipped"}:
                    logger.warning(
                        "drain %s task#%s: %s %s",
                        task_type,
                        result.get("task_id"),
                        status,
                        result.get("error", ""),
                    )
        if not progressed:
            return counts


async def _sweep_stale(pool: Any) -> None:
    """좀비(active) 작업 회수(H3): 핸들러 도중 죽어 ``running`` 으로 남은 작업을 timeout 후
    ``retrying`` 으로 승계해 재claim 가능하게 한다. 드레인 데몬이 곧 큐 처리 주체이므로
    orphan 복구를 여기에 직접 배선한다(ops 데몬 플래그(HIRING_OPS_DAEMON_ENABLED)에 의존하지 않음).
    """
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    async with pool.acquire() as conn:
        swept = await ProcessingQueueRepository(conn).sweep_stale_active_tasks()
    if swept.get("retried_count") or swept.get("failed_count"):
        logger.info("좀비 작업 회수: %s", swept)


async def run_drain_daemon(
    pool: Any, settings: Settings, *, handler_factory: HandlerFactory = build_task_handlers
) -> None:
    """주기 루프: 좀비 회수 → 큐를 idle 까지 드레인 → interval 대기 → 반복. cancel 은 전파."""
    try:
        while True:
            try:
                await _sweep_stale(pool)
                counts = await drain_until_idle(pool, handler_factory=handler_factory)
                if counts:
                    logger.info("드레인 패스 완료: %s", counts)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 한 틱 실패가 데몬을 죽이지 않게
                logger.exception("드레인 틱 실패 — 다음 주기에 재시도")
            await asyncio.sleep(settings.queue_drain_interval_sec)
    except asyncio.CancelledError:
        logger.info("드레인 데몬 cancelled")
        raise


async def supervise_queue_daemon(pool: Any, settings: Settings) -> None:
    """run_drain_daemon 감시: advisory lock 단일화 + 예기치 못한 죽음이면 60초 후 재기동.

    락을 못 잡으면(다른 워커/컨테이너가 이미 드레인 중) 폴링하지 않고 60초마다 재시도한다.
    락 보유자가 죽으면 세션이 끊겨 락이 풀리므로 자동 승계된다(ops/price 데몬과 동일).
    """
    while True:
        try:
            async with pool.acquire() as lock_conn:
                locked = await lock_conn.fetchval(
                    "SELECT pg_try_advisory_lock($1)", _DRAIN_ADVISORY_LOCK_KEY
                )
                if not locked:
                    logger.warning(
                        "드레인 데몬 락 보유 중인 인스턴스 존재 — %.0fs 후 재시도",
                        _RESTART_DELAY_SEC,
                    )
                else:
                    await run_drain_daemon(pool, settings)
                    logger.error("드레인 데몬이 예기치 않게 종료됨 — 재기동")
        except asyncio.CancelledError:
            logger.info("드레인 데몬 supervisor cancelled")
            raise
        except Exception:  # noqa: BLE001 - 데몬은 죽지 않고 재기동한다
            logger.exception("드레인 데몬 crash — %.0fs 후 재기동", _RESTART_DELAY_SEC)
        await asyncio.sleep(_RESTART_DELAY_SEC)
