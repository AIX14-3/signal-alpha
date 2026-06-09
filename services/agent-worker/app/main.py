from fastapi import FastAPI

from app.api.routes.dart import router as dart_router
from app.api.routes.health import router as health_router
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
    app.include_router(queue_router)
    app.include_router(schedules_router)
    app.include_router(tasks_router)
    return app


app = create_app()
