from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

TaskHandler = Callable[[Mapping[str, Any]], Awaitable[dict[str, Any] | None]]


class QueueTaskRunner:
    def __init__(self, connection: Any, handlers: dict[str, TaskHandler]) -> None:
        from signal_alpha_data_access.repositories import ProcessingQueueRepository

        self._connection = connection
        self._handlers = handlers
        self._queue_repository = ProcessingQueueRepository(connection)

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


def _should_retry(task: Mapping[str, Any]) -> bool:
    retry_count = int(task.get("retry_count") or 0)
    max_retry_count = int(task.get("max_retry_count") or 0)
    return retry_count < max_retry_count
