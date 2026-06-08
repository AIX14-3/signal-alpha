from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends

from app.core.database import get_database_pool
from app.orchestrator.handlers import build_task_handlers
from app.orchestrator.tasks import QueueTaskRunner, TaskHandler

router = APIRouter(prefix="/internal/tasks", tags=["tasks"])

TaskHandlerFactory = Callable[[Any], dict[str, TaskHandler]]


def get_task_handler_factory() -> TaskHandlerFactory:
    return build_task_handlers


@router.post("/{task_type}/run")
async def run_next_task(
    task_type: str,
    pool: Any = Depends(get_database_pool),
    handler_factory: TaskHandlerFactory = Depends(get_task_handler_factory),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        handlers = handler_factory(connection)
        return await QueueTaskRunner(connection, handlers).run_next(task_type)
