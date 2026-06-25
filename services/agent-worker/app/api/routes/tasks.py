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


class FullPipelineRequest(BaseModel):
    stock_code: str
    signal_date: str | None = None
    priority: str = "batch"
    include_dart_collection: bool = False


@router.post("/pipeline/{stock_id}/run")
async def run_full_pipeline(
    stock_id: int,
    request: FullPipelineRequest,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """Fan one stock out across every report source (PRICE/ALTERNATIVE/DART) + a
    fan-in AGGREGATE. Drain the queue per task_type afterwards to materialize the
    blended report."""
    from datetime import date

    from app.orchestrator.full_pipeline import enqueue_stock_pipeline
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    signal_date = date.fromisoformat(request.signal_date) if request.signal_date else date.today()
    async with pool.acquire() as connection:
        enqueued = await enqueue_stock_pipeline(
            ProcessingQueueRepository(connection),
            stock_id=stock_id,
            stock_code=request.stock_code,
            signal_date=signal_date,
            priority=request.priority,
            include_dart_collection=request.include_dart_collection,
        )
    return {"stock_id": stock_id, "enqueued": enqueued}


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
