from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.database import get_database_pool
from app.orchestrator.tasks import QueueTaskRunner, TaskHandler

router = APIRouter(prefix="/internal/tasks", tags=["tasks"])


def get_task_handlers() -> dict[str, TaskHandler]:
    return {}


@router.post("/{task_type}/run")
async def run_next_task(
    task_type: str,
    pool: Any = Depends(get_database_pool),
    handlers: dict[str, TaskHandler] = Depends(get_task_handlers),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        return await QueueTaskRunner(connection, handlers).run_next(task_type)
