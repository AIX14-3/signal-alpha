"""어드민 큐 운영 — overview·stale sweep·retry·dead-letter replay/reconcile.

worker 운영 API 프록시 + 백엔드 스케줄 헬스 요약을 합쳐 대시보드에 노출한다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.routes.admin._serializers import (
    _queue_ops_events,
    _schedule_health_summary,
    _schedule_row,
)
from app.api.routes.admin._worker import _worker_request
from app.api.routes.admin_auth import get_current_admin
from app.core.config import Settings, get_settings
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import CollectionScheduleRepository, parse_schedule_row

router = APIRouter()


class QueueSweepRequest(BaseModel):
    running_timeout_minutes: int = 30
    retrying_timeout_minutes: int = 120


class DeadLetterReplayRequest(BaseModel):
    dead_letter_ids: list[int]


class DeadLetterReconcileRequest(BaseModel):
    limit: int = 100


@router.get("/queue/overview")
async def admin_queue_overview(
    _admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    queue = await _worker_request("GET", "/internal/stats/queue", settings=settings)
    failed_tasks = await _worker_request(
        "GET",
        "/internal/queue/tasks",
        settings=settings,
        params={"status": "failed", "limit": 20},
    )
    dead_letters = await _worker_request(
        "GET",
        "/internal/queue/dead-letter",
        settings=settings,
        params={"replayed": False, "limit": 20},
    )
    async with pool.acquire() as connection:
        schedule_rows = await CollectionScheduleRepository(connection).list_all()
    schedules = [_schedule_row(parse_schedule_row(row)) for row in schedule_rows]
    schedule_summary = _schedule_health_summary(schedules)
    return {
        "queue": queue,
        "failed_tasks": failed_tasks,
        "dead_letters": dead_letters,
        "schedule_summary": schedule_summary,
        "events": _queue_ops_events(queue, failed_tasks, dead_letters, schedule_summary),
    }


@router.post("/queue/sweep-stale")
async def admin_sweep_stale_queue(
    payload: QueueSweepRequest,
    _admin: dict[str, Any] = Depends(get_current_admin),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return await _worker_request(
        "POST",
        "/internal/queue/sweep-stale",
        settings=settings,
        json_body=payload.model_dump(),
    )


@router.post("/queue/tasks/{task_id}/retry")
async def admin_retry_queue_task(
    task_id: int,
    _admin: dict[str, Any] = Depends(get_current_admin),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return await _worker_request(
        "POST",
        f"/internal/queue/tasks/{task_id}/retry",
        settings=settings,
    )


@router.post("/queue/dead-letter/replay")
async def admin_replay_dead_letters(
    payload: DeadLetterReplayRequest,
    _admin: dict[str, Any] = Depends(get_current_admin),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return await _worker_request(
        "POST",
        "/internal/queue/dead-letter/replay",
        settings=settings,
        json_body=payload.model_dump(),
    )


@router.post("/queue/dead-letter/reconcile")
async def admin_reconcile_dead_letters(
    payload: DeadLetterReconcileRequest,
    _admin: dict[str, Any] = Depends(get_current_admin),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return await _worker_request(
        "POST",
        "/internal/queue/dead-letter/reconcile",
        settings=settings,
        json_body=payload.model_dump(),
    )
