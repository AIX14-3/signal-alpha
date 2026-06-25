from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import get_settings
from app.core.database import get_database_pool

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(pool: Any = Depends(get_database_pool)) -> dict[str, str]:
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
        "version": settings.version
    }
