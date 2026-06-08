from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Signal Alpha Main Server",
        version=settings.version,
        summary="User-facing API boundary for Signal Alpha"
    )
    app.include_router(health_router)
    return app


app = create_app()
