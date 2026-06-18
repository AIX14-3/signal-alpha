from fastapi import FastAPI

from app.api.routes.auth import auth_router, users_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.api.routes.signals import router as signals_router
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
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(stocks_router)
    app.include_router(watchlists_router)
    app.include_router(dashboard_router)
    app.include_router(signals_router)
    return app


app = create_app()
