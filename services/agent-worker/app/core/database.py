from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request

from app.core.config import get_settings


@asynccontextmanager
async def lifespan_with_database(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.database_pool = None
    app.state.price_collector_task = None

    if settings.database_url:
        from signal_alpha_data_access import DatabaseSettings, create_pool

        app.state.database_pool = await create_pool(
            DatabaseSettings(database_url=settings.database_url)
        )

    if settings.price_collector_enabled and app.state.database_pool is not None:
        from app.collectors.price.runner import supervise_daemon

        app.state.price_collector_task = asyncio.create_task(
            supervise_daemon(app.state.database_pool, settings),
            name="price-collector-daemon",
        )

    try:
        yield
    finally:
        # 순서 고정: 데몬 cancel → 완료 대기 → pool.close
        # (역순이면 데몬이 닫힌 풀을 쓰다 InterfaceError)
        task = getattr(app.state, "price_collector_task", None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        pool = getattr(app.state, "database_pool", None)
        if pool is not None:
            await pool.close()


def get_database_pool(request: Request) -> Any:
    pool = getattr(request.app.state, "database_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool is not configured.")
    return pool
