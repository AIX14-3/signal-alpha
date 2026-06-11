from fastapi import FastAPI

from app.api.routes.report import router as report_router
from app.api.routes.health import router as health_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Signal Alpha Agent Worker",
        version=settings.version,
        summary="Internal data collection and LLM analysis worker for Signal Alpha",
    )
    app.include_router(health_router)
    app.include_router(report_router)
    return app


app = create_app()
