from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import get_settings
from app.core.database import get_database_pool

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(
    request: Request,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    # DB 연결을 실제로 확인해 장애 시 503 을 반환한다. 풀 미구성(DATABASE_URL 미설정)은
    # get_database_pool 의존성이 이미 503 으로 처리한다. Cloud Run/GCE 헬스체크가
    # DB 단절을 감지해 트래픽 차단/재시작하도록 하기 위함.
    try:
        async with pool.acquire() as connection:
            await connection.fetchval("SELECT 1")
    except Exception as exc:  # 연결/쿼리 실패 → unhealthy
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc

    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.service_name,
        "version": settings.version,
        "runtime": {
            "price_collector": {
                "enabled": settings.price_collector_enabled,
                "state": _task_state(getattr(request.app.state, "price_collector_task", None)),
            },
            "hiring_ops_daemon": {
                "enabled": settings.hiring_ops_daemon_enabled,
                "state": _task_state(getattr(request.app.state, "ops_daemon_task", None)),
            },
            "queue_drain_daemon": {
                "enabled": settings.queue_drain_daemon_enabled,
                "state": _task_state(getattr(request.app.state, "queue_drain_task", None)),
                **_queue_drain_status(
                    getattr(request.app.state, "queue_drain_status", None)
                ),
            },
        },
    }


def _task_state(task: Any | None) -> str:
    if task is None:
        return "not_started"
    if task.cancelled():
        return "cancelled"
    if task.done():
        return "stopped"
    return "running"


def _queue_drain_status(status: Any | None) -> dict[str, Any]:
    empty = {
        "cycles_completed": 0,
        "last_started_at": None,
        "last_finished_at": None,
        "last_cycle": None,
        "last_error": None,
    }
    if status is None:
        return empty
    return {**empty, **status.snapshot()}
