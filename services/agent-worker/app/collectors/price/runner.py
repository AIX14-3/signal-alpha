"""Realtime price collector runtime (Kiwoom REST API, polling).

agent-worker 프로세스 안에서 lifespan 백그라운드 태스크로 돌아간다
(독립 컨테이너 아님). 단일 uvicorn 워커 전제 — 멀티 워커면 데몬이 중복
기동되므로 금지. 일회성 수집은 ``POST /internal/price/collect``로 대체.

Targets are read from the shared ``stocks`` table (``is_target = TRUE``).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

import httpx
from signal_alpha_data_access.repositories.collection import CollectionRepository

from app.collectors.price.kiwoom.auth import TokenManager
from app.collectors.price.kiwoom.rest_client import KiwoomRestClient
from app.collectors.price.market_hours import (
    is_market_open,
    next_market_open,
    now_kst,
    parse_hhmm,
)
from app.collectors.price.pipeline import (
    CycleStats,
    run_investor_flow_update,
    run_snapshot_cycle,
)
from app.collectors.price.rate_limiter import RateLimiter
from app.collectors.price.repository import PriceSnapshotRepository
from app.core.config import Settings

logger = logging.getLogger("price_collector")

_IDLE_SLEEP_CAP_SEC = 300.0
_RESTART_DELAY_SEC = 60.0


def build_client(settings: Settings, http: httpx.AsyncClient) -> KiwoomRestClient:
    tokens = TokenManager(
        http=http,
        api_base=settings.kiwoom_api_base,
        app_key=settings.kiwoom_app_key,
        app_secret=settings.kiwoom_app_secret,
    )
    limiter = RateLimiter(settings.kiwoom_min_request_interval_sec)
    return KiwoomRestClient(
        http=http,
        api_base=settings.kiwoom_api_base,
        token_manager=tokens,
        rate_limiter=limiter,
    )


def _run_status(stats: CycleStats) -> str:
    if stats.failed and stats.stored == 0:
        return "failed"
    if stats.failed:
        return "partial"
    return "success"


async def _finish_run(runs: CollectionRepository, run_id: int, stats: CycleStats) -> None:
    await runs.finish_collector_run(
        run_id=run_id,
        status=_run_status(stats),
        collected_count=stats.collected,
        inserted_count=stats.stored,
        skipped_count=stats.skipped,
        failed_count=stats.failed,
        error_message="; ".join(stats.errors[:5]) or None,
    )


async def run_once(pool: Any, settings: Settings, *, flows_only: bool) -> CycleStats:
    repository = PriceSnapshotRepository(pool)
    runs = CollectionRepository(pool)
    targets = await repository.list_target_stocks()
    if not targets:
        logger.warning("no target stocks (stocks.is_target = TRUE); nothing to collect")
        return CycleStats()

    run_id = await runs.create_collector_run("PRICE", "manual")
    try:
        async with httpx.AsyncClient(timeout=settings.kiwoom_timeout_seconds) as http:
            client = build_client(settings, http)
            if flows_only:
                stats = await run_investor_flow_update(
                    client, repository, targets, now_kst().date()
                )
            else:
                stats = await run_snapshot_cycle(client, repository, targets, now_kst())
    except BaseException as exc:
        # 예외(취소 포함)로 빠져나가도 collector_runs 행을 'running'으로 남기지 않는다.
        await asyncio.shield(
            runs.finish_collector_run(
                run_id=run_id,
                status="failed",
                collected_count=0,
                inserted_count=0,
                skipped_count=0,
                failed_count=0,
                error_message=str(exc)[:500] or type(exc).__name__,
            )
        )
        raise
    await _finish_run(runs, run_id, stats)
    return stats


async def run_daemon(pool: Any, settings: Settings) -> None:
    open_time = parse_hhmm(settings.market_open)
    close_time = parse_hhmm(settings.market_close)

    repository = PriceSnapshotRepository(pool)
    runs = CollectionRepository(pool)

    session_run_id: int | None = None
    session_stats = CycleStats()
    flows_done_for: date | None = None

    async with httpx.AsyncClient(timeout=settings.kiwoom_timeout_seconds) as http:
        client = build_client(settings, http)
        try:
            while True:
                now = now_kst()
                if is_market_open(now, open_time, close_time):
                    if session_run_id is None:
                        session_run_id = await runs.create_collector_run("PRICE", "batch")
                        session_stats = CycleStats()
                        logger.info("market open — polling session %s started", session_run_id)
                    targets = await repository.list_target_stocks()
                    stats = await run_snapshot_cycle(client, repository, targets, now)
                    session_stats.merge(stats)
                    logger.info(
                        "cycle done: collected=%d stored=%d skipped=%d failed=%d",
                        stats.collected,
                        stats.stored,
                        stats.skipped,
                        stats.failed,
                    )
                    await asyncio.sleep(settings.price_poll_interval_sec)
                    continue

                if session_run_id is not None:
                    await _finish_run(runs, session_run_id, session_stats)
                    logger.info("market closed — polling session %s finished", session_run_id)
                    session_run_id = None

                flow_due = now.replace(
                    hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0
                ) + timedelta(minutes=settings.price_flow_delay_after_close_min)
                if (
                    now.weekday() < 5
                    and now >= flow_due
                    and flows_done_for != now.date()
                ):
                    targets = await repository.list_target_stocks()
                    run_id = await runs.create_collector_run("PRICE", "batch")
                    stats = await run_investor_flow_update(
                        client, repository, targets, now.date()
                    )
                    await _finish_run(runs, run_id, stats)
                    flows_done_for = now.date()
                    logger.info("investor flow update for %s done", now.date())

                wake_at = next_market_open(now, open_time)
                sleep_sec = min((wake_at - now).total_seconds(), _IDLE_SLEEP_CAP_SEC)
                await asyncio.sleep(max(sleep_sec, 1.0))
        except asyncio.CancelledError:
            # 종료 시점에 열린 폴링 세션을 collector_runs에 마감해 둔다.
            if session_run_id is not None:
                await asyncio.shield(_finish_run(runs, session_run_id, session_stats))
                logger.info("shutdown — polling session %s finished", session_run_id)
            raise


# Postgres advisory lock 키 — 데몬 인스턴스가 동시에 2개 이상 뜨는 것을 막는다
# (uvicorn 멀티 워커, 컨테이너 중복 기동 모두 커버). 임의의 고정 상수.
_ADVISORY_LOCK_KEY = 0x50524943  # "PRIC"


async def supervise_daemon(pool: Any, settings: Settings) -> None:
    """run_daemon을 감시: 예기치 못한 죽음이면 60초 후 재기동, cancel은 전파.

    advisory lock을 못 잡으면(다른 워커/컨테이너가 수집 중) 폴링하지 않고
    60초마다 재시도한다 — 락 보유자가 죽으면 세션이 끊겨 락이 풀리므로
    자동으로 승계된다.
    """
    while True:
        try:
            async with pool.acquire() as lock_conn:
                locked = await lock_conn.fetchval(
                    "SELECT pg_try_advisory_lock($1)", _ADVISORY_LOCK_KEY
                )
                if not locked:
                    logger.warning(
                        "price collector lock held by another instance; retrying in %.0fs",
                        _RESTART_DELAY_SEC,
                    )
                else:
                    # 락은 세션 단위 — 커넥션이 풀로 반환될 때 asyncpg reset이
                    # pg_advisory_unlock_all()로 해제한다.
                    await run_daemon(pool, settings)
                    logger.error("price collector daemon exited unexpectedly; restarting")
        except asyncio.CancelledError:
            logger.info("price collector daemon cancelled")
            raise
        except Exception:  # noqa: BLE001 - 데몬은 죽지 않고 재기동한다
            logger.exception("price collector daemon crashed; restarting in %.0fs",
                             _RESTART_DELAY_SEC)
        await asyncio.sleep(_RESTART_DELAY_SEC)
