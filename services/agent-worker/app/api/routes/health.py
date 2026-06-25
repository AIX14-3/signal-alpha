from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request) -> Any:
    """liveness + DB readiness. DB 미연결이면 503 으로 표면화한다(배포 헬스체크용).

    프로세스가 떠 있어도 DB 가 끊기면 'ok' 를 반환하던 기존 동작은 알람을 무력화한다.
    풀에서 커넥션을 얻어 ``SELECT 1`` 로 실제 연결을 확인한다.
    """
    settings = get_settings()
    body: dict[str, str] = {
        "status": "ok",
        "service": settings.service_name,
        "version": settings.version,
        "db": "ok",
    }
    pool = getattr(request.app.state, "database_pool", None)
    if pool is None:
        return JSONResponse(
            status_code=503, content={**body, "status": "unavailable", "db": "uninitialized"}
        )
    try:
        async with pool.acquire() as connection:
            await connection.execute("SELECT 1")
    except Exception:  # noqa: BLE001 — 어떤 DB 오류든 unhealthy 로 보고
        return JSONResponse(
            status_code=503, content={**body, "status": "unavailable", "db": "error"}
        )
    return body
