from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_database_pool
from app.orchestrator.queue.handlers import build_task_handlers
from app.orchestrator.queue.tasks import (
    DEFAULT_CYCLE_PLAN,
    QueueCycleRunner,
    QueueTaskRunner,
    TaskHandler,
)

router = APIRouter(prefix="/internal/queue", tags=["queue"])

TaskHandlerFactory = Callable[[Any], dict[str, TaskHandler]]


def get_task_handler_factory() -> TaskHandlerFactory:
    return build_task_handlers


class SweepStaleTasksRequest(BaseModel):
    running_timeout_minutes: int = Field(default=30, ge=1)
    retrying_timeout_minutes: int = Field(default=120, ge=1)


class RunBatchRequest(BaseModel):
    max_runs: int = Field(default=20, ge=1, le=100)


class RunCycleRequest(BaseModel):
    # task_type → 한 사이클당 캡. 미지정 시 DEFAULT_CYCLE_PLAN(수집기 특성별 차등) 사용.
    plan: dict[str, int] | None = None
    max_passes: int = Field(default=10000, ge=1, le=1000000)


@router.get("/tasks")
async def list_tasks(
    stock_code: str | None = None,
    task_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    async with pool.acquire() as connection:
        rows = await ProcessingQueueRepository(connection).list_tasks(
            stock_code=stock_code,
            task_type=task_type,
            status=status,
            limit=limit,
        )
    items = [dict(row) for row in rows]
    return {"count": len(items), "items": items}


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: int, pool: Any = Depends(get_database_pool)) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    async with pool.acquire() as connection:
        row = await ProcessingQueueRepository(connection).retry_task(task_id=task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Retryable task not found.")
    return dict(row)


@router.post("/{task_type}/claim")
async def claim_next_task(task_type: str, pool: Any = Depends(get_database_pool)) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    async with pool.acquire() as connection:
        row = await ProcessingQueueRepository(connection).claim_next_pending(task_type=task_type)

    if row is None:
        raise HTTPException(status_code=404, detail="No pending task.")

    return dict(row)


@router.post("/{task_type}/run-batch")
async def run_task_batch(
    task_type: str,
    request: RunBatchRequest,
    pool: Any = Depends(get_database_pool),
    handler_factory: TaskHandlerFactory = Depends(get_task_handler_factory),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        runner = QueueTaskRunner(connection, handler_factory(connection))
        results: list[dict[str, Any]] = []
        for _ in range(request.max_runs):
            result = await runner.run_next(task_type)
            if result["status"] == "idle":
                break
            results.append(result)
            if result["status"] == "failed":
                break
    return {
        "task_type": task_type,
        "run_count": len(results),
        "max_runs": request.max_runs,
        "max_runs_reached": len(results) >= request.max_runs,
        "results": results,
    }


@router.post("/run-cycle")
async def run_queue_cycle(
    request: RunCycleRequest,
    pool: Any = Depends(get_database_pool),
    handler_factory: TaskHandlerFactory = Depends(get_task_handler_factory),
) -> dict[str, Any]:
    """task_type 간 공정 라운드로빈 drain — 어떤 한 type 도 다른 type 을 굶기지 않는다.

    기존 "task_type 을 고정 순서로 max_runs 까지 연속 처리(=큐가 빌 때까지)" 드레인을
    대체한다. plan 의 각 type 에서 한 패스당 1개씩 순환 처리한다.
    """
    plan = request.plan or DEFAULT_CYCLE_PLAN
    async with pool.acquire() as connection:
        runner = QueueTaskRunner(connection, handler_factory(connection))
        summary = await QueueCycleRunner(runner).run_cycle(plan, max_passes=request.max_passes)
    return {"plan": plan, **summary}


@router.post("/sweep-stale")
async def sweep_stale_tasks(
    request: SweepStaleTasksRequest,
    pool: Any = Depends(get_database_pool),
) -> dict[str, int]:
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    async with pool.acquire() as connection:
        return await ProcessingQueueRepository(connection).sweep_stale_active_tasks(
            running_timeout_minutes=request.running_timeout_minutes,
            retrying_timeout_minutes=request.retrying_timeout_minutes,
        )
