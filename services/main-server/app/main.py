from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.admin import admin_router
from app.api.routes.analytics import analytics_router
from app.api.routes.auth import auth_router, users_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.api.routes.journals import router as journals_router
from app.api.routes.payments import router as payments_router
from app.api.routes.reports import router as reports_router
from app.api.routes.signals import router as signals_router
from app.api.routes.subscriptions import subscriptions_router
from app.api.routes.watchlists import stocks_router, watchlists_router
from app.core.config import get_settings
from app.core.database import lifespan_with_database


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Signal Alpha Main Server",
        version=settings.version,
        summary="User-facing API boundary for Signal Alpha",
        lifespan=lifespan_with_database
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(stocks_router)
    app.include_router(watchlists_router)
    app.include_router(dashboard_router)
    app.include_router(signals_router)
    app.include_router(reports_router)
    app.include_router(journals_router)
    app.include_router(subscriptions_router)
    app.include_router(payments_router)
    app.include_router(admin_router)
    app.include_router(analytics_router)
    return app


app = create_app()
