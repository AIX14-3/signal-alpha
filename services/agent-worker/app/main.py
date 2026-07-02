import hmac

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
        lifespan=lifespan_with_database,
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
        """/internal/* requires a configured X-Internal-Token shared secret."""
        if request.url.path.startswith("/internal"):
            token = settings.internal_api_token.strip()
            if not token:
                return JSONResponse(
                    {
                        "detail": {
                            "code": "INTERNAL_AUTH_NOT_CONFIGURED",
                            "message": "internal API token is not configured",
                        }
                    },
                    status_code=503,
                )
            provided = request.headers.get("X-Internal-Token") or ""
            # 상수 시간 비교 — 단순 != 는 타이밍 부채널로 토큰 접두 일치를 노출할 수 있다.
            # 빈 토큰(미설정)은 위의 503 fail-closed 가 먼저 처리한다.
            if not hmac.compare_digest(provided.encode("utf-8"), token.encode("utf-8")):
                return JSONResponse(
                    {"detail": {"code": "INTERNAL_AUTH_REQUIRED", "message": "invalid internal token"}},
                    status_code=401,
                )
        return await call_next(request)

    return app


app = create_app()
