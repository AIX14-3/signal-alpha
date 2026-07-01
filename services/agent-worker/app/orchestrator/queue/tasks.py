from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

TaskHandler = Callable[[Mapping[str, Any]], Awaitable[dict[str, Any] | None]]


class QueueTaskRunner:
    def __init__(self, connection: Any, handlers: dict[str, TaskHandler]) -> None:
        from signal_alpha_data_access.repositories import (
            DeadLetterRepository,
            ProcessingQueueRepository,
        )

        self._connection = connection
        self._handlers = handlers
        self._queue_repository = ProcessingQueueRepository(connection)
        self._dead_letter_repository = DeadLetterRepository(connection)

    async def run_next(self, task_type: str) -> dict[str, Any]:
        return await self.run_task(task_type)

    async def run_task(self, task_type: str, *, task_id: int | None = None) -> dict[str, Any]:
        if task_id is None:
            task = await self._queue_repository.claim_next_pending(task_type=task_type)
        else:
            task = await self._queue_repository.claim_pending_by_id(
                task_id=task_id,
                task_type=task_type,
            )
        if task is None:
            return {"status": "idle", "task_type": task_type}

        task_id = task["id"]
        handler = self._handlers.get(task_type)
        if handler is None:
            await self._queue_repository.mark_skipped(
                task_id=task_id,
                message=f"No handler registered for task type: {task_type}",
            )
            return {"status": "skipped", "task_id": task_id, "task_type": task_type}

        try:
            payload = await handler(task)
        except Exception as exc:
            retry = _should_retry(task)
            await self._queue_repository.mark_failed(
                task_id=task_id,
                error_message=str(exc),
                retry=retry,
            )
            if not retry:
                # 종착(재시도 소진) — dead_letter 로 즉시 아카이브. task 는 claim 시점의
                # 행이라 retry_count 는 pre-increment 값 → +1 이 최종 재시도 횟수.
                await self._archive_terminal_failure(task, str(exc))
            return {
                "status": "failed",
                "task_id": task_id,
                "task_type": task_type,
                "retry": retry,
                "error": str(exc),
            }

        await self._queue_repository.mark_success(task_id=task_id)
        return {
            "status": "success",
            "task_id": task_id,
            "task_type": task_type,
            "result": payload or {},
        }

    async def _archive_terminal_failure(self, task: Mapping[str, Any], error: str) -> None:
        await self._dead_letter_repository.archive_failed_task(
            processing_queue_id=task["id"],
            stock_id=task.get("stock_id"),
            task_type=task["task_type"],
            priority=task["priority"],
            source_raw_ids=task.get("source_raw_ids"),
            source_signal_event_ids=task.get("source_signal_event_ids"),
            source_analysis_result_ids=task.get("source_analysis_result_ids"),
            task_context=task.get("task_context"),
            final_error_message=error,
            final_retry_count=int(task.get("retry_count") or 0) + 1,
        )


def _should_retry(task: Mapping[str, Any]) -> bool:
    retry_count = int(task.get("retry_count") or 0)
    max_retry_count = int(task.get("max_retry_count") or 0)
    return retry_count < max_retry_count


# 공정 라운드로빈 drain 기본 계획 — task_type → 한 사이클당 최대 처리 수(캡).
# 수집기 특성에 맞춘 차등: DART/Report 수집은 무거워 작게(2), PRICE/집계는 가벼워 크게.
# 핵심: 어떤 한 task_type도 다른 type을 굶기지 못한다(한 패스에 type당 1개씩 순환).
# Alternative(hiring/patent/datalab)는 수집은 독립 일배치이고 여기엔 분석/정규화 type만 편입.
DEFAULT_CYCLE_PLAN: dict[str, int] = {
    # 무거운 수집 — 작은 캡으로 throttle (collect_dart 는 문서 fetch 상한과 병행)
    "collect_dart": 2,
    "collect_report": 2,
    # DART 후속 (가벼움)
    "normalize_dart": 10,
    "analyze_dart": 10,
    # Report 후속
    "process_report": 5,
    "normalize_report": 10,
    "analyze_report": 10,
    # Report RAG 임베딩 — PDF 재추출 + 임베딩 API 호출이라 무거움 → 작은 캡으로 throttle.
    "embed_report": 3,
    # Alternative 정규화/보강/분석 (수집은 독립 일배치)
    "NORMALIZE_HIRING": 10,
    "NORMALIZE_PATENT": 10,
    "NORMALIZE_DATALAB": 10,
    "ENRICH_PATENT": 10,
    "ENRICH_HIRING": 10,
    # 대체데이터 소스별 분석 스테이지 (C안 Phase 3 — 1태스크=1소스)
    "ANALYZE_DATALAB": 20,
    "ANALYZE_HIRING": 20,
    "ANALYZE_PATENT": 20,
    # PRICE — 빠름(DB만 읽음), 큰 캡
    "analyze_price": 50,
    # ML/메타/집계/발행 (가벼움)
    "src_infer": 20,
    "return_combine": 20,
    "aggregate_signal": 30,
    "synthesize": 20,
    "publish_signals": 20,
}


class QueueCycleRunner:
    """task_type 간 공정 라운드로빈 drain.

    한 "패스"마다 계획(plan)의 각 task_type 에서 **최대 1개**씩만 처리하고, 캡이 남은
    type 이 더 있으면 다음 패스로 순환한다. 따라서 느린 type(collect_dart)이 한 번에
    하나만 처리되어 다른 type 을 굶기지 못한다. 기존 "한 type 을 max_runs 까지 연속
    처리(=큐가 빌 때까지)" 동작을 대체한다.

    종료: 모든 type 이 idle 이거나 캡 소진(=진전 없는 패스) 또는 max_passes 도달.
    """

    def __init__(self, runner: QueueTaskRunner) -> None:
        self._runner = runner

    async def run_cycle(
        self, plan: Mapping[str, int], *, max_passes: int = 10000
    ) -> dict[str, Any]:
        remaining: dict[str, int] = {t: int(c) for t, c in plan.items() if int(c) > 0}
        counts: dict[str, int] = {t: 0 for t in remaining}
        statuses: dict[str, int] = {}
        failures: dict[str, int] = {}
        idle: set[str] = set()
        passes = 0
        stopped_reason = "max_passes_reached"

        while passes < max_passes:
            passes += 1
            progressed = False
            for task_type in list(remaining.keys()):
                if remaining[task_type] <= 0 or task_type in idle:
                    continue
                result = await self._runner.run_next(task_type)
                status = result.get("status")
                if status == "idle":
                    idle.add(task_type)
                    continue
                # 처리됨(success/failed/skipped) — 캡 1 소모. failed 여도 사이클을 멈추지
                # 않는다(다른 type 굶기지 않기 위해). run_next 가 이미 재시도/사장 처리.
                remaining[task_type] -= 1
                counts[task_type] += 1
                statuses[status] = statuses.get(status, 0) + 1
                progressed = True
                if status == "failed":
                    failures[task_type] = failures.get(task_type, 0) + 1
            if not progressed:
                stopped_reason = "idle"
                break
            if all(remaining[t] <= 0 or t in idle for t in remaining):
                stopped_reason = "plan_exhausted"
                break

        return {
            "passes": passes,
            "total_runs": sum(counts.values()),
            "counts": {t: c for t, c in counts.items() if c > 0},
            "statuses": statuses,
            "failures": failures,
            "idle_task_types": sorted(idle),
            "stopped_reason": stopped_reason,
        }
