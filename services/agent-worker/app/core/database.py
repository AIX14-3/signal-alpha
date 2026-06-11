from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request

from app.core.config import get_settings


@asynccontextmanager
async def lifespan_with_database(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.database_pool = None

    if settings.database_url:
        from signal_alpha_data_access import DatabaseSettings, create_pool

        app.state.database_pool = await create_pool(
            DatabaseSettings(database_url=settings.database_url)
        )

    try:
        yield
    finally:
        pool = getattr(app.state, "database_pool", None)
        if pool is not None:
            await pool.close()


def get_database_pool(request: Request) -> Any:
    pool = getattr(request.app.state, "database_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool is not configured.")
    return pool
