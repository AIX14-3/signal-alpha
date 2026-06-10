from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.database import get_database_pool
from app.orchestrator.queue.handlers import build_task_handlers
from app.orchestrator.queue.tasks import QueueTaskRunner, TaskHandler

router = APIRouter(prefix="/internal/tasks", tags=["tasks"])

TaskHandlerFactory = Callable[[Any], dict[str, TaskHandler]]


def get_task_handler_factory() -> TaskHandlerFactory:
    return build_task_handlers


class EnqueueTaskRequest(BaseModel):
    stock_id: int
    priority: str = "batch"
    source_raw_ids: list[int] | None = None
    source_signal_event_ids: list[int] | None = None
    source_analysis_result_ids: list[int] | None = None
    task_context: dict[str, Any] | None = None
    dedupe: bool = Field(default=True)


@router.post("/{task_type}/enqueue")
async def enqueue_task(
    task_type: str,
    request: EnqueueTaskRequest,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    async with pool.acquire() as connection:
        task_id = await ProcessingQueueRepository(connection).enqueue(
            stock_id=request.stock_id,
            task_type=task_type,
            priority=request.priority,
            source_raw_ids=request.source_raw_ids,
            source_signal_event_ids=request.source_signal_event_ids,
            source_analysis_result_ids=request.source_analysis_result_ids,
            task_context=request.task_context,
            dedupe=request.dedupe,
        )
    return {"task_id": task_id, "task_type": task_type}


@router.post("/{task_type}/run")
async def run_next_task(
    task_type: str,
    pool: Any = Depends(get_database_pool),
    handler_factory: TaskHandlerFactory = Depends(get_task_handler_factory),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        handlers = handler_factory(connection)
        return await QueueTaskRunner(connection, handlers).run_next(task_type)
