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
