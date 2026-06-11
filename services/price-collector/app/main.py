"""Realtime price collector entrypoint (Kiwoom REST API, polling).

Runs as a long-lived process (Docker friendly):

    python -m app.main            # daemon: poll during market hours,
                                  # confirmed investor flows after close
    python -m app.main --once     # single snapshot sweep, then exit
    python -m app.main --flows    # single investor-flow update, then exit

Targets are read from the shared ``stocks`` table (``is_target = TRUE``).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date, timedelta

import httpx
from dotenv import load_dotenv
from signal_alpha_data_access.repositories.collection import CollectionRepository

from app.core.config import Settings, get_settings
from app.core.market_hours import (
    is_market_open,
    next_market_open,
    now_kst,
    parse_hhmm,
)
from app.core.rate_limiter import RateLimiter
from app.kiwoom.auth import TokenManager
from app.kiwoom.rest_client import KiwoomRestClient
from app.pipeline import CycleStats, run_investor_flow_update, run_snapshot_cycle
from app.storage.repository import PriceSnapshotRepository

logger = logging.getLogger("price_collector")

_IDLE_SLEEP_CAP_SEC = 300.0


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


async def _connect_db(settings: Settings):
    import asyncpg

    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required.")
    return await asyncpg.connect(dsn=settings.database_url)


def _run_status(stats: CycleStats) -> str:
    if stats.failed and stats.stored == 0:
        return "failed"
    if stats.failed:
        return "partial"
    return "success"


async def run_once(settings: Settings, *, flows_only: bool) -> CycleStats:
    connection = await _connect_db(settings)
    try:
        repository = PriceSnapshotRepository(connection)
        runs = CollectionRepository(connection)
        targets = await repository.list_target_stocks()
        if not targets:
            logger.warning("no target stocks (stocks.is_target = TRUE); nothing to collect")
            return CycleStats()

        run_id = await runs.create_collector_run("PRICE", "manual")
        async with httpx.AsyncClient(timeout=settings.kiwoom_timeout_seconds) as http:
            client = build_client(settings, http)
            if flows_only:
                stats = await run_investor_flow_update(
                    client, repository, targets, now_kst().date()
                )
            else:
                stats = await run_snapshot_cycle(client, repository, targets, now_kst())
        await runs.finish_collector_run(
            run_id=run_id,
            status=_run_status(stats),
            collected_count=stats.collected,
            inserted_count=stats.stored,
            skipped_count=stats.skipped,
            failed_count=stats.failed,
            error_message="; ".join(stats.errors[:5]) or None,
        )
        return stats
    finally:
        await connection.close()


async def run_daemon(settings: Settings) -> None:
    open_time = parse_hhmm(settings.market_open)
    close_time = parse_hhmm(settings.market_close)

    connection = await _connect_db(settings)
    repository = PriceSnapshotRepository(connection)
    runs = CollectionRepository(connection)

    session_run_id: int | None = None
    session_stats = CycleStats()
    flows_done_for: date | None = None

    async with httpx.AsyncClient(timeout=settings.kiwoom_timeout_seconds) as http:
        client = build_client(settings, http)
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
                await asyncio.sleep(settings.poll_interval_sec)
                continue

            if session_run_id is not None:
                await runs.finish_collector_run(
                    run_id=session_run_id,
                    status=_run_status(session_stats),
                    collected_count=session_stats.collected,
                    inserted_count=session_stats.stored,
                    skipped_count=session_stats.skipped,
                    failed_count=session_stats.failed,
                    error_message="; ".join(session_stats.errors[:5]) or None,
                )
                logger.info("market closed — polling session %s finished", session_run_id)
                session_run_id = None

            flow_due = now.replace(
                hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0
            ) + timedelta(minutes=settings.flow_delay_after_close_min)
            if (
                now.weekday() < 5
                and now >= flow_due
                and flows_done_for != now.date()
            ):
                targets = await repository.list_target_stocks()
                run_id = await runs.create_collector_run("PRICE", "batch")
                stats = await run_investor_flow_update(client, repository, targets, now.date())
                await runs.finish_collector_run(
                    run_id=run_id,
                    status=_run_status(stats),
                    collected_count=stats.collected,
                    inserted_count=stats.stored,
                    skipped_count=stats.skipped,
                    failed_count=stats.failed,
                    error_message="; ".join(stats.errors[:5]) or None,
                )
                flows_done_for = now.date()
                logger.info("investor flow update for %s done", now.date())

            wake_at = next_market_open(now, open_time)
            sleep_sec = min((wake_at - now).total_seconds(), _IDLE_SLEEP_CAP_SEC)
            await asyncio.sleep(max(sleep_sec, 1.0))


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Signal Alpha realtime price collector")
    parser.add_argument("--once", action="store_true", help="run one snapshot sweep and exit")
    parser.add_argument("--flows", action="store_true", help="run one investor-flow update and exit")
    args = parser.parse_args()

    settings = get_settings()
    if args.once or args.flows:
        stats = asyncio.run(run_once(settings, flows_only=args.flows))
        logger.info(
            "done: collected=%d stored=%d skipped=%d failed=%d",
            stats.collected,
            stats.stored,
            stats.skipped,
            stats.failed,
        )
        return
    asyncio.run(run_daemon(settings))


if __name__ == "__main__":
    main()
