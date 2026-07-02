"""Queue drain daemon for consuming processing_queue toward publication."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.config import Settings
from app.orchestrator.queue.handlers import build_task_handlers
from app.orchestrator.queue.tasks import DEFAULT_CYCLE_PLAN, QueueCycleRunner, QueueTaskRunner
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
    REQUERY_SOURCE,
    RETURN_COMBINE,
    SRC_INFER,
    SYNTHESIZE,
)

logger = logging.getLogger("queue_drain_daemon")

_DRAIN_ADVISORY_LOCK_KEY = 0x5144524E
_RESTART_DELAY_SEC = 60.0

DRAIN_ORDER: tuple[str, ...] = (
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
    ANALYZE_DATALAB,
    ANALYZE_HIRING,
    ANALYZE_PATENT,
    ANALYZE_PRICE,
    SRC_INFER,
    RETURN_COMBINE,
    # 오케스트레이터 되묻기는 AGGREGATE 앞에 둔다: 한 드레인 사이클에서 문제 소스 재분석 →
    # 재종합 순으로 흐르게(되묻기가 새 소스 결과를 남기고, 이어 AGGREGATE 가 재블렌드). 되묻기가
    # 재인큐한 AGGREGATE 는 dedupe + requery_round 상한으로 유한하다(무한루프 없음).
    REQUERY_SOURCE,
    AGGREGATE_SIGNAL,
    SYNTHESIZE,
    PUBLISH_SIGNALS,
)


def _resolve_order(handler_keys: Any) -> list[str]:
    """Prefer the chain order, then append registered task types not yet listed."""
    known = set(handler_keys)
    ordered = [task_type for task_type in DRAIN_ORDER if task_type in known]
    extra = [task_type for task_type in handler_keys if task_type not in set(DRAIN_ORDER)]
    return ordered + extra


HandlerFactory = Callable[[Any], dict[str, Any]]


class QueueDrainRuntimeStatus:
    def __init__(self) -> None:
        self.cycles_completed = 0
        self.last_started_at: str | None = None
        self.last_finished_at: str | None = None
        self.last_cycle: dict[str, Any] | None = None
        self.last_error: str | None = None

    def mark_started(self) -> None:
        self.last_started_at = _utc_now()

    def mark_cycle(self, summary: Mapping[str, Any]) -> None:
        self.cycles_completed += 1
        self.last_finished_at = _utc_now()
        self.last_cycle = dict(summary)
        self.last_error = None

    def mark_error(self, exc: BaseException) -> None:
        self.last_finished_at = _utc_now()
        self.last_error = str(exc)

    def snapshot(self) -> dict[str, Any]:
        return {
            "cycles_completed": self.cycles_completed,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "last_cycle": self.last_cycle,
            "last_error": self.last_error,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def drain_until_idle(
    pool: Any, *, handler_factory: HandlerFactory = build_task_handlers
) -> dict[str, int]:
    """Legacy full drain helper kept for tests and one-off compatibility."""
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


async def run_drain_cycle(
    pool: Any,
    *,
    handler_factory: HandlerFactory = build_task_handlers,
    plan: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Run one bounded fair queue cycle using the same runner as /internal/queue/run-cycle."""
    async with pool.acquire() as conn:
        handlers = handler_factory(conn)
        runner = QueueTaskRunner(conn, handlers)
        return await QueueCycleRunner(runner).run_cycle(plan or DEFAULT_CYCLE_PLAN)


async def _sweep_stale(pool: Any) -> None:
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    async with pool.acquire() as conn:
        swept = await ProcessingQueueRepository(conn).sweep_stale_active_tasks()
    if swept.get("retried_count") or swept.get("failed_count"):
        logger.info("swept stale queue tasks: %s", swept)


async def run_drain_daemon(
    pool: Any,
    settings: Settings,
    *,
    handler_factory: HandlerFactory = build_task_handlers,
    runtime_status: QueueDrainRuntimeStatus | None = None,
) -> None:
    """Run bounded fair queue cycles until cancelled."""
    try:
        while True:
            try:
                if runtime_status is not None:
                    runtime_status.mark_started()
                await _sweep_stale(pool)
                summary = await run_drain_cycle(pool, handler_factory=handler_factory)
                if runtime_status is not None:
                    runtime_status.mark_cycle(summary)
                if summary.get("total_runs"):
                    logger.info("queue drain cycle completed: %s", summary)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - daemon must survive cycle failures
                if runtime_status is not None:
                    runtime_status.mark_error(exc)
                logger.exception("queue drain cycle failed; retrying next interval")
            await asyncio.sleep(settings.queue_drain_interval_sec)
    except asyncio.CancelledError:
        logger.info("queue drain daemon cancelled")
        raise


async def supervise_queue_daemon(
    pool: Any,
    settings: Settings,
    *,
    runtime_status: QueueDrainRuntimeStatus | None = None,
) -> None:
    """Hold one advisory lock and restart the daemon loop after unexpected exits."""
    while True:
        try:
            async with pool.acquire() as lock_conn:
                locked = await lock_conn.fetchval(
                    "SELECT pg_try_advisory_lock($1)", _DRAIN_ADVISORY_LOCK_KEY
                )
                if not locked:
                    logger.warning(
                        "queue drain daemon lock is held elsewhere; retrying in %.0fs",
                        _RESTART_DELAY_SEC,
                    )
                else:
                    await run_drain_daemon(
                        pool,
                        settings,
                        runtime_status=runtime_status,
                    )
                    logger.error("queue drain daemon exited unexpectedly; restarting")
        except asyncio.CancelledError:
            logger.info("queue drain daemon supervisor cancelled")
            raise
        except Exception:  # noqa: BLE001 - supervisor should retry after crashes
            logger.exception("queue drain daemon crashed; retrying in %.0fs", _RESTART_DELAY_SEC)
        await asyncio.sleep(_RESTART_DELAY_SEC)
