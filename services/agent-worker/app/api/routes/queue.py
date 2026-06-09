from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_database_pool

router = APIRouter(prefix="/internal/queue", tags=["queue"])


class SweepStaleTasksRequest(BaseModel):
    running_timeout_minutes: int = Field(default=30, ge=1)
    retrying_timeout_minutes: int = Field(default=120, ge=1)


@router.post("/{task_type}/claim")
async def claim_next_task(task_type: str, pool: Any = Depends(get_database_pool)) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    async with pool.acquire() as connection:
        row = await ProcessingQueueRepository(connection).claim_next_pending(task_type=task_type)

    if row is None:
        raise HTTPException(status_code=404, detail="No pending task.")

    return dict(row)


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
