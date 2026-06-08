from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.core.database import get_database_pool

router = APIRouter(prefix="/internal/queue", tags=["queue"])


@router.post("/{task_type}/claim")
async def claim_next_task(task_type: str, pool: Any = Depends(get_database_pool)) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    async with pool.acquire() as connection:
        row = await ProcessingQueueRepository(connection).claim_next_pending(task_type=task_type)

    if row is None:
        raise HTTPException(status_code=404, detail="No pending task.")

    return dict(row)
