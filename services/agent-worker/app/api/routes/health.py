from datetime import datetime, timezone
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
            "publishing": _publishing_status(settings),
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


@router.get("/health/live")
async def liveness(request: Request) -> dict[str, Any]:
    # 프로세스 liveness — DB 도달성이 아니라 **큐 드레인 데몬의 전진**을 검사한다. readiness(/health)
    # 는 DB 단절 시 503 이지만, 드레인 데몬이 라이브락에 빠져도 /health 는 200 을 반환해 무한정 얼어붙는
    # 신호를 못 잡는다. 여기서는 데몬이 enabled 인데 (a) task 가 running 이 아니거나 (b) 마지막 사이클
    # 완료가 임계 초과로 정체되면 503 → k8s livenessProbe 가 pod 를 재시작(자가치유). 데몬 비활성
    # 프로세스(예: collector-only)는 항상 200.
    settings = get_settings()
    detail = _drain_liveness(
        settings,
        task=getattr(request.app.state, "queue_drain_task", None),
        status=getattr(request.app.state, "queue_drain_status", None),
    )
    if not detail["alive"]:
        raise HTTPException(status_code=503, detail=detail)
    return {"status": "ok", **detail}


def _drain_liveness(settings: Any, *, task: Any | None, status: Any | None) -> dict[str, Any]:
    if not getattr(settings, "queue_drain_daemon_enabled", False):
        return {"alive": True, "reason": "drain_daemon_disabled"}
    task_state = _task_state(task)
    if task_state != "running":
        return {"alive": False, "reason": f"drain_task_{task_state}", "task_state": task_state}
    snapshot = status.snapshot() if status is not None else {}
    # cycles_completed 는 매 사이클 무조건 증가 → 마지막 완료 시각 정체 = 정지. 아직 첫 사이클을
    # 못 마쳤으면 시작 시각으로 대체(초기 기동은 initialDelaySeconds 가 커버).
    marker = snapshot.get("last_finished_at") or snapshot.get("last_started_at")
    if marker is None:
        return {"alive": True, "reason": "starting", "cycles_completed": snapshot.get("cycles_completed", 0)}
    age = _age_seconds(marker)
    max_stale = float(getattr(settings, "queue_drain_liveness_max_stale_sec", 30.0))
    if age is not None and age > max_stale:
        return {
            "alive": False,
            "reason": "drain_stalled",
            "age_seconds": round(age, 1),
            "max_stale_seconds": max_stale,
            "cycles_completed": snapshot.get("cycles_completed", 0),
            "last_error": snapshot.get("last_error"),
        }
    return {
        "alive": True,
        "reason": "progressing",
        "age_seconds": round(age, 1) if age is not None else None,
        "cycles_completed": snapshot.get("cycles_completed", 0),
    }


def _age_seconds(iso_marker: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(iso_marker)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _publishing_status(settings: Any) -> dict[str, Any]:
    backend_configured = bool(getattr(settings, "backend_database_url", None))
    if backend_configured:
        return {
            "backend_database_configured": True,
            "mode": "backend_db",
            "status": "ready",
            "warning": None,
        }
    return {
        "backend_database_configured": False,
        "mode": "single_db_noop",
        "status": "disabled",
        "warning": "BACKEND_DATABASE_URL is not configured; PUBLISH_SIGNALS tasks are skipped.",
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
