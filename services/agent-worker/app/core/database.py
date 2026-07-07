from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan_with_database(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.database_pool = None
    # 백엔드(서비스) DB 발행용 풀 (#531). BACKEND_DATABASE_URL 미설정이면 None(단일 DB 모드).
    app.state.backend_database_pool = None
    app.state.price_collector_task = None
    app.state.ops_daemon_task = None
    app.state.queue_drain_task = None
    app.state.queue_drain_status = None
    app.state.guard_task = None
    app.state.guard_status = None
    app.state.news_task = None
    app.state.news_status = None

    if settings.database_url:
        from signal_alpha_data_access import DatabaseSettings, create_pool

        app.state.database_pool = await create_pool(
            DatabaseSettings(database_url=settings.database_url)
        )

    if settings.backend_database_url:
        from signal_alpha_data_access import DatabaseSettings, create_pool

        app.state.backend_database_pool = await create_pool(
            DatabaseSettings(database_url=settings.backend_database_url)
        )

    if settings.price_collector_enabled:
        if app.state.database_pool is None:
            logger.warning(
                "PRICE_COLLECTOR_ENABLED but DATABASE_URL is not set; "
                "price collector will not start"
            )
        elif not (settings.kiwoom_app_key and settings.kiwoom_app_secret):
            logger.warning(
                "PRICE_COLLECTOR_ENABLED but KIWOOM_APP_KEY/KIWOOM_APP_SECRET are empty; "
                "price collector will not start"
            )
        else:
            from app.collectors.price.runner import supervise_daemon

            app.state.price_collector_task = asyncio.create_task(
                supervise_daemon(app.state.database_pool, settings),
                name="price-collector-daemon",
            )

    # Hiring 운영 알림 + self-healing 데몬 (Phase 5) — collector_runs 통계 기반
    # Discord 알림 + sweep/reconcile 자동화. 기본 off. price 와 동일 lifespan 패턴.
    if settings.hiring_ops_daemon_enabled:
        if app.state.database_pool is None:
            logger.warning(
                "HIRING_OPS_DAEMON_ENABLED but DATABASE_URL is not set; "
                "ops daemon will not start"
            )
        else:
            from app.observability.ops_daemon import supervise_ops_daemon

            app.state.ops_daemon_task = asyncio.create_task(
                supervise_ops_daemon(app.state.database_pool, settings),
                name="hiring-ops-daemon",
            )

    # 큐 드레인 데몬 (워커 영역 완성 #11) — processing_queue 를 끝단(발행)까지 연속 소비.
    # 기본 off. 단일 기동을 advisory lock 으로 보장(ops/price 와 동일 패턴).
    if settings.queue_drain_daemon_enabled:
        if app.state.database_pool is None:
            logger.warning(
                "QUEUE_DRAIN_DAEMON_ENABLED but DATABASE_URL is not set; "
                "queue drain daemon will not start"
            )
        else:
            from app.orchestrator.queue.drain_daemon import (
                QueueDrainRuntimeStatus,
                supervise_queue_daemon,
            )

            app.state.queue_drain_status = QueueDrainRuntimeStatus()
            app.state.queue_drain_task = asyncio.create_task(
                supervise_queue_daemon(
                    app.state.database_pool,
                    settings,
                    runtime_status=app.state.queue_drain_status,
                ),
                name="queue-drain-daemon",
            )

    # 지정학 리스크 Kill-Switch 감시 데몬 — guard_* 테이블은 backend DB 소유이므로
    # backend 풀로만 돈다. BACKEND_DATABASE_URL 미설정(로컬 leak 가드)이면 기동하지 않음.
    if settings.guard_enabled:
        if app.state.backend_database_pool is None:
            logger.warning(
                "GUARD_ENABLED but BACKEND_DATABASE_URL is not set; guard daemon will not start"
            )
        else:
            from app.guard.daemon import GuardRuntimeStatus, supervise_guard_daemon

            app.state.guard_status = GuardRuntimeStatus()
            app.state.guard_task = asyncio.create_task(
                supervise_guard_daemon(
                    app.state.backend_database_pool,
                    settings,
                    runtime_status=app.state.guard_status,
                ),
                name="guard-daemon",
            )

    # 종목별 뉴스 수집 데몬 — stock_news 는 backend DB 소유(guard 와 동일 계약)이므로
    # backend 풀로만 돈다. BACKEND_DATABASE_URL 미설정이면 기동하지 않음.
    if settings.news_enabled:
        if app.state.backend_database_pool is None:
            logger.warning(
                "NEWS_ENABLED but BACKEND_DATABASE_URL is not set; news daemon will not start"
            )
        else:
            from app.news.daemon import NewsRuntimeStatus, supervise_news_daemon

            app.state.news_status = NewsRuntimeStatus()
            app.state.news_task = asyncio.create_task(
                supervise_news_daemon(
                    app.state.backend_database_pool,
                    settings,
                    runtime_status=app.state.news_status,
                ),
                name="news-daemon",
            )

    try:
        yield
    finally:
        # 순서 고정: 데몬 cancel → 완료 대기 → pool.close
        # (역순이면 데몬이 닫힌 풀을 쓰다 InterfaceError)
        for attr in (
            "price_collector_task",
            "ops_daemon_task",
            "queue_drain_task",
            "guard_task",
            "news_task",
        ):
            task = getattr(app.state, attr, None)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        for attr in ("database_pool", "backend_database_pool"):
            pool = getattr(app.state, attr, None)
            if pool is not None:
                await pool.close()


def get_database_pool(request: Request) -> Any:
    pool = getattr(request.app.state, "database_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool is not configured.")
    return pool


def get_backend_database_pool(request: Request) -> Any:
    """백엔드 DB 발행용 풀. BACKEND_DATABASE_URL 미설정이면 None(단일 DB 모드)."""
    return getattr(request.app.state, "backend_database_pool", None)
