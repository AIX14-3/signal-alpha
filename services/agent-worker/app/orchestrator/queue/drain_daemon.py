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
    COLLECT_DART_EMPLOYEE,
    COLLECT_DART_FINANCIALS,
    COLLECT_DART_OWNERSHIP,
    COLLECT_REPORT,
    ENRICH_HIRING,
    ENRICH_PATENT,
    NORMALIZE_DART,
    NORMALIZE_DART_EMPLOYEE,
    NORMALIZE_DART_FINANCIALS,
    NORMALIZE_DART_OWNERSHIP,
    NORMALIZE_DATALAB,
    NORMALIZE_HIRING,
    NORMALIZE_PATENT,
    NORMALIZE_REPORT,
    PROCESS_REPORT,
    PUBLISH_SIGNALS,
    RECORD_EPISODE_OUTCOMES,
    REQUERY_SOURCE,
    SCORE_COHORT,
    SYNTHESIZE,
)

logger = logging.getLogger("queue_drain_daemon")

_DRAIN_ADVISORY_LOCK_KEY = 0x5144524E
_RESTART_DELAY_SEC = 60.0

DRAIN_ORDER: tuple[str, ...] = (
    COLLECT_DART,
    COLLECT_DART_OWNERSHIP,
    COLLECT_DART_FINANCIALS,
    COLLECT_DART_EMPLOYEE,
    NORMALIZE_DART_OWNERSHIP,
    NORMALIZE_DART_FINANCIALS,
    NORMALIZE_DART_EMPLOYEE,
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
    # LLM 코호트 채점 — per-stock ANALYZE_* 를 대체하는 배치 채점(플래그 게이트). AGGREGATE
    # 앞에 둬 같은 드레인 사이클에서 채점 → 재종합 순으로 흐르게 한다.
    SCORE_COHORT,
    # 오케스트레이터 되묻기는 AGGREGATE 앞에 둔다: 한 드레인 사이클에서 문제 소스 재분석 →
    # 재종합 순으로 흐르게(되묻기가 새 소스 결과를 남기고, 이어 AGGREGATE 가 재블렌드). 되묻기가
    # 재인큐한 AGGREGATE 는 dedupe + requery_round 상한으로 유한하다(무한루프 없음).
    REQUERY_SOURCE,
    AGGREGATE_SIGNAL,
    SYNTHESIZE,
    PUBLISH_SIGNALS,
    # 사후 유지태스크 — 발행 경로와 무관(맨 끝). 발행 후 만기된 에피소드 outcome 을 채운다.
    RECORD_EPISODE_OUTCOMES,
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


async def _seed_episode_outcome_task(pool: Any, settings: Settings) -> None:
    """일 1회 에피소드 아웃컴 리코더 태스크를 재시드(중복 방지 가드).

    종목 무관 유지태스크라 여느 소스처럼 스케줄러 HTTP 로 인큐되지 않는다 — 대신 드레인 데몬이
    ``_sweep_stale`` 과 같은 결에서 직접 시드한다. 열린(pending/running/retrying) 태스크가 있거나
    최근 ``episode_outcome_interval_sec`` 내 완료분이 있으면 시드하지 않아 하루 한 건만 흐른다
    (stock_id=NULL — 전체 스윕). 리코더 자체가 멱등·degrade 라 과다 시드돼도 안전하지만, 이 가드로
    큐를 깔끔히 유지한다.
    """
    if not getattr(settings, "episode_outcome_enabled", True):
        return
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    async with pool.acquire() as conn:
        should_seed = await conn.fetchval(
            """
            SELECT NOT EXISTS (
                SELECT 1
                FROM processing_queue
                WHERE task_type = $1
                  AND (
                      status IN ('pending', 'running', 'retrying')
                      OR finished_at > NOW() - make_interval(secs => $2)
                  )
            )
            """,
            RECORD_EPISODE_OUTCOMES,
            float(settings.episode_outcome_interval_sec),
        )
        if should_seed:
            await ProcessingQueueRepository(conn).enqueue(
                stock_id=None,
                task_type=RECORD_EPISODE_OUTCOMES,
                priority="batch",
            )
            logger.info("seeded episode outcome recorder task")


async def _seed_cohort_score_tasks(pool: Any, settings: Settings) -> None:
    """LLM 코호트 채점 태스크 일 1회 시드 (LLM_SCORING_ENABLED 게이트, off 면 no-op).

    가드/멱등은 producer 가 소유(열린 태스크·최근 완료분 있으면 재시드하지 않음 —
    ``_seed_episode_outcome_task`` 와 같은 결). 시드 실패는 드레인 사이클을 못 막는다."""
    if not settings.llm_scoring_enabled:
        return
    try:
        from app.orchestrator.cohort.producer import seed_cohort_tasks

        await seed_cohort_tasks(pool, settings)
    except Exception:  # noqa: BLE001 — daemon must survive seed failures
        logger.exception("SCORE_COHORT seed failed; retrying next interval")


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
                await _seed_episode_outcome_task(pool, settings)
                await _seed_cohort_score_tasks(pool, settings)
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
