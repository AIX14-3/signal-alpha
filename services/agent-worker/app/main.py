from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes.dart import router as dart_router
from app.api.routes.dead_letter import router as dead_letter_router
from app.api.routes.health import router as health_router
from app.api.routes.observability import router as observability_router
from app.api.routes.price import router as price_router
from app.api.routes.queue import router as queue_router
from app.api.routes.schedules import router as schedules_router
from app.api.routes.tasks import router as tasks_router
from app.core.config import get_settings
from app.core.database import lifespan_with_database


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Signal Alpha Agent Worker",
        version=settings.version,
        summary="Internal data collection and LLM analysis worker for Signal Alpha",
        lifespan=lifespan_with_database
    )
    app.include_router(health_router)
    app.include_router(dart_router)
    app.include_router(price_router)
    app.include_router(queue_router)
    app.include_router(dead_letter_router)
    app.include_router(observability_router)
    app.include_router(schedules_router)
    app.include_router(tasks_router)

    @app.middleware("http")
    async def _require_internal_token(request: Request, call_next):  # type: ignore[no-untyped-def]
        """INTERNAL_API_TOKEN 설정 시 /internal/* 는 X-Internal-Token 일치를 요구.

        토큰 미설정이면 검사하지 않는다(기존 동작 — 네트워크 격리 전제). /health 등
        /internal 외 경로는 항상 통과.
        """
        token = settings.internal_api_token
        if token and request.url.path.startswith("/internal"):
            if request.headers.get("X-Internal-Token") != token:
                return JSONResponse(
                    {"detail": {"code": "INTERNAL_AUTH_REQUIRED", "message": "invalid internal token"}},
                    status_code=401,
                )
        return await call_next(request)

    return app


app = create_app()
