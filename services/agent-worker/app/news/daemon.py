"""종목별 뉴스 수집 데몬 — 활성 종목을 라운드로빈으로 순회하며 Naver 뉴스 적재.

guard 데몬과 동일한 supervise(advisory lock)/run(생존 루프) 패턴. pool 은 backend
DB 풀(stock_news 소유 DB)이어야 한다. 종목별 수집·적재 실패는 상태를 바꾸지 않고
다음 종목/사이클로 넘어간다(fail-safe).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.clients.naver_news_client import NaverNewsClient, NaverNewsError
from app.news.collector import collect_stock_news
from app.news.repository import insert_news_batch, last_collected_map, list_active_stocks

logger = logging.getLogger("news_daemon")

_NEWS_ADVISORY_LOCK_KEY = 0x4E455753  # "NEWS" — guard(0x47554152)/price/drain 과 상이
_RESTART_DELAY_SEC = 60.0


class NewsRuntimeStatus:
    def __init__(self) -> None:
        self.cycles_completed = 0
        self.last_started_at: str | None = None
        self.last_finished_at: str | None = None
        self.last_cycle: dict[str, Any] | None = None
        self.last_error: str | None = None

    def mark_started(self) -> None:
        self.last_started_at = _utc_now()

    def mark_cycle(self, summary: Mapping[str, Any]) -> None:
        self.cycles_completed += 1
        self.last_finished_at = _utc_now()
        self.last_cycle = dict(summary)
        self.last_error = None

    def mark_error(self, exc: BaseException) -> None:
        self.last_finished_at = _utc_now()
        self.last_error = str(exc)

    def snapshot(self) -> dict[str, Any]:
        return {
            "cycles_completed": self.cycles_completed,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "last_cycle": self.last_cycle,
            "last_error": self.last_error,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_client(settings: Any) -> NaverNewsClient:
    return NaverNewsClient(
        client_id=settings.naver_client_id,
        client_secret=settings.naver_client_secret,
        timeout_seconds=int(settings.news_fetch_timeout_seconds),
    )


def _select_due(
    stocks: list[dict[str, Any]],
    last_collected: Mapping[int, datetime],
    *,
    refresh_hours: float,
    batch_size: int,
    now: datetime,
) -> list[dict[str, Any]]:
    """미수집 종목 우선 + 마지막 수집이 refresh_hours 이전인 종목을 오래된 순으로 batch_size 만큼."""
    threshold = now - timedelta(hours=refresh_hours)
    due: list[tuple[datetime | None, dict[str, Any]]] = []
    for stock in stocks:
        last = last_collected.get(int(stock["id"]))
        if last is None or last <= threshold:
            due.append((last, stock))
    # 미수집(None) 을 맨 앞으로, 그 뒤 오래된 수집순.
    due.sort(key=lambda pair: (pair[0] is not None, pair[0] or now))
    return [stock for _, stock in due[: max(1, batch_size)]]


async def run_news_cycle(
    pool: Any,
    settings: Any,
    *,
    client: NaverNewsClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """1회 수집 사이클. due 종목 배치를 순회하며 stock_news 적재. pool 은 backend DB 풀."""
    now = now or datetime.now(timezone.utc)
    client = client or _default_client(settings)

    stocks = await list_active_stocks(pool)
    if not stocks:
        return {"due": 0, "processed": 0, "inserted": 0}
    last_collected = await last_collected_map(pool)
    due = _select_due(
        stocks,
        last_collected,
        refresh_hours=settings.news_refresh_hours,
        batch_size=settings.news_batch_size,
        now=now,
    )

    processed = 0
    total_inserted = 0
    for stock in due:
        query = (stock.get("name") or stock.get("ticker") or "").strip()
        if not query:
            continue
        try:
            items = await collect_stock_news(
                client,
                query,
                lookback_days=settings.news_lookback_days,
                max_items=settings.news_max_items,
                now=now,
            )
        except NaverNewsError as exc:
            # 종목 단위 실패는 넘어간다(쿼터·일시 오류) — 다음 종목/사이클에서 재시도.
            logger.warning("news fetch failed for %s: %s", stock.get("ticker"), exc)
            continue
        processed += 1
        if not items:
            continue
        async with pool.acquire() as connection:
            async with connection.transaction():
                total_inserted += await insert_news_batch(
                    connection,
                    stock_id=int(stock["id"]),
                    ticker=stock["ticker"],
                    items=items,
                )

    return {"due": len(due), "processed": processed, "inserted": total_inserted}


async def run_news_daemon(
    pool: Any,
    settings: Any,
    *,
    runtime_status: NewsRuntimeStatus | None = None,
    client: NaverNewsClient | None = None,
) -> None:
    try:
        while True:
            try:
                if runtime_status is not None:
                    runtime_status.mark_started()
                summary = await run_news_cycle(pool, settings, client=client)
                if runtime_status is not None:
                    runtime_status.mark_cycle(summary)
                if summary.get("inserted"):
                    logger.info("news cycle completed: %s", summary)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 데몬은 사이클 실패에도 생존
                if runtime_status is not None:
                    runtime_status.mark_error(exc)
                logger.exception("news cycle failed; retrying next interval")
            await asyncio.sleep(settings.news_poll_interval_sec)
    except asyncio.CancelledError:
        logger.info("news daemon cancelled")
        raise


async def supervise_news_daemon(
    pool: Any,
    settings: Any,
    *,
    runtime_status: NewsRuntimeStatus | None = None,
) -> None:
    """advisory lock 으로 단일 기동 보장 + 비정상 종료 시 재기동."""
    while True:
        try:
            async with pool.acquire() as lock_conn:
                locked = await lock_conn.fetchval(
                    "SELECT pg_try_advisory_lock($1)", _NEWS_ADVISORY_LOCK_KEY
                )
                if not locked:
                    logger.warning(
                        "news daemon lock is held elsewhere; retrying in %.0fs",
                        _RESTART_DELAY_SEC,
                    )
                else:
                    await run_news_daemon(pool, settings, runtime_status=runtime_status)
                    logger.error("news daemon exited unexpectedly; restarting")
        except asyncio.CancelledError:
            logger.info("news daemon supervisor cancelled")
            raise
        except Exception:  # noqa: BLE001 - supervisor 는 크래시 후 재시도
            logger.exception("news daemon crashed; retrying in %.0fs", _RESTART_DELAY_SEC)
        await asyncio.sleep(_RESTART_DELAY_SEC)
