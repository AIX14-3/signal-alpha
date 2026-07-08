import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes.admin import admin_router
from app.api.routes.analytics import analytics_router
from app.api.routes.auth import auth_router, users_router
from app.api.routes.brief import router as brief_router
from app.api.routes.community import router as community_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.guard import admin_router as guard_admin_router
from app.api.routes.guard import router as guard_router
from app.api.routes.health import router as health_router
from app.api.routes.journals import router as journals_router
from app.api.routes.market import router as market_router
from app.api.routes.methodology import router as methodology_router
from app.api.routes.payments import router as payments_router
from app.api.routes.postmortem import router as postmortem_router
from app.api.routes.reports import router as reports_router
from app.api.routes.signals import router as signals_router
from app.api.routes.subscriptions import subscriptions_router
from app.api.routes.watchlists import news_router, stocks_router, watchlists_router
from app.core.config import get_settings
from app.core.database import lifespan_with_database
from app.core.rate_limit import RateLimitMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.core.throttle import get_auth_rate_limiter

logger = logging.getLogger(__name__)

# 레이트리밋 대상: 인증/로그인 계열 POST (브루트포스 방어).
_AUTH_RATE_LIMIT_PATHS = (
    "/api/auth/login",
    "/api/auth/signup",
    "/api/auth/social/login",
    "/api/admin/login",
)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Signal Alpha Main Server",
        version=settings.version,
        summary="User-facing API boundary for Signal Alpha",
        lifespan=lifespan_with_database,
    )

    # 미들웨어는 마지막 추가가 가장 바깥. SecurityHeaders 를 바깥에 둬 모든 응답(차단 응답 포함)에
    # 헤더가 붙도록 한다. 요청 흐름: SecurityHeaders → TrustedHost → CORS → RateLimit → app.
    app.add_middleware(
        RateLimitMiddleware,
        limiter=get_auth_rate_limiter(),
        enabled=settings.rate_limit_enabled,
        path_prefixes=_AUTH_RATE_LIMIT_PATHS,
    )
    if settings.rate_limit_enabled:
        # ⚠️ FixedWindowLimiter·LoginLockout 은 프로세스 인메모리 상태다(레플리카 간 비공유,
        # 재시작 시 초기화). 단일 워커(replicas=1)에서만 정확하다 — 수평 확장하려면 Redis 등
        # 공유 저장소로 교체해야 브루트포스 방어가 유지된다(그 전까지 main-server replicas=1 고정).
        logger.warning(
            "RateLimit/LoginLockout use in-process state (single-worker only); "
            "scaling main-server replicas>1 requires a shared store (Redis)."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        # 와일드카드 대신 명시(실제 사용하는 메서드/헤더만 허용).
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    if settings.security_headers_enabled:
        app.add_middleware(SecurityHeadersMiddleware, hsts=settings.hsts_enabled)

    # 처리되지 않은 예외는 내부 정보(스택/DB/외부API 메시지)를 숨기고 일반화된 500 으로 응답.
    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": {"code": "INTERNAL_ERROR", "message": "서버 오류가 발생했습니다."}},
        )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(stocks_router)
    app.include_router(watchlists_router)
    app.include_router(news_router)
    app.include_router(dashboard_router)
    app.include_router(signals_router)
    app.include_router(brief_router)
    app.include_router(guard_router)
    app.include_router(guard_admin_router)
    app.include_router(reports_router)
    app.include_router(journals_router)
    app.include_router(postmortem_router)
    app.include_router(methodology_router)
    app.include_router(community_router)
    app.include_router(market_router)
    app.include_router(subscriptions_router)
    app.include_router(payments_router)
    app.include_router(admin_router)
    app.include_router(analytics_router)
    return app


app = create_app()
