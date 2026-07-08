"""종목별 뉴스 수집 데몬 — 활성 종목을 라운드로빈으로 순회하며 Naver 뉴스 적재.

guard 데몬과 동일한 supervise(advisory lock)/run(생존 루프) 패턴. pool 은 backend
DB 풀(stock_news 소유 DB)이어야 한다. 종목별 수집·적재 실패는 상태를 바꾸지 않고
다음 종목/사이클로 넘어간다(fail-safe).
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.clients.anthropic_client import AnthropicError, AnthropicJsonClient
from app.clients.naver_news_client import NaverNewsClient, NaverNewsError
from app.narrate.base import NarrateError
from app.news import digest as digest_mod
from app.news.collector import collect_stock_news
from app.news.repository import (
    get_digest_meta,
    insert_news_batch,
    last_collected_map,
    list_active_stocks,
    list_recent_articles,
    upsert_digest,
)

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


def _default_digest_client(settings: Any) -> AnthropicJsonClient | None:
    """NEWS_LLM_ENABLED + anthropic 키가 있을 때만 digest 클라이언트. 부재 시 None(폴백)."""
    if not getattr(settings, "news_llm_enabled", False):
        return None
    provider = (getattr(settings, "news_llm_provider", "anthropic") or "").strip().lower()
    if provider != "anthropic":
        logger.warning("news digest: unsupported provider %r; digest disabled", provider)
        return None
    key = getattr(settings, "anthropic_api_key", "") or ""
    if not key:
        logger.warning("news digest: ANTHROPIC_API_KEY missing; digest disabled")
        return None
    try:
        return AnthropicJsonClient(
            api_key=key,
            model=settings.news_llm_model,
            timeout=float(settings.news_llm_timeout_seconds),
        )
    except AnthropicError as exc:
        logger.warning("news digest: client init failed: %s", exc)
        return None


def _candidate_window(
    candidates: list[dict[str, Any]],
) -> tuple[datetime | None, datetime | None]:
    times = [c["published_at"] for c in candidates if isinstance(c.get("published_at"), datetime)]
    if not times:
        return None, None
    return min(times), max(times)


async def _maybe_generate_digest(
    connection: Any,
    dclient: AnthropicJsonClient,
    settings: Any,
    stock: dict[str, Any],
    *,
    now: datetime,
) -> str:
    """한 종목 digest 를 생성·UPSERT. 반환: digested|skipped|failed. 실패는 사이클을 막지 않는다."""
    stock_id = int(stock["id"])
    ticker = stock["ticker"]
    stock_name = (stock.get("name") or ticker or "").strip()
    candidate_cap = int(settings.news_digest_candidates)
    try:
        articles = await list_recent_articles(
            connection, stock_id=stock_id, limit=max(candidate_cap * 3, candidate_cap)
        )
        candidates = digest_mod.select_candidates(
            articles, stock_name=stock_name, ticker=ticker, limit=candidate_cap
        )
        if not candidates:
            return "skipped"
        shash = digest_mod.source_hash(candidates)
        meta = await get_digest_meta(connection, stock_id)
        if meta is not None:
            if meta.get("source_hash") == shash:
                return "skipped"  # 동일 기사집합 → 멱등 skip(비용 0)
            min_hours = float(getattr(settings, "news_digest_min_interval_hours", 0) or 0)
            generated_at = meta.get("generated_at")
            if min_hours > 0 and isinstance(generated_at, datetime):
                if now - generated_at < timedelta(hours=min_hours):
                    return "skipped"  # 하한 간격 미경과 → 다음 사이클로 이연
        prompt = digest_mod.build_digest_prompt(candidates, stock_name=stock_name)
        result = await dclient.generate_json(prompt, schema=digest_mod.DIGEST_SCHEMA)
        digest_text, article_count = digest_mod.validate_digest(
            result, candidate_ids={c["id"] for c in candidates}
        )
        window_start, window_end = _candidate_window(candidates)
        await upsert_digest(
            connection,
            stock_id=stock_id,
            ticker=ticker,
            digest_text=digest_text,
            model=dclient.model,
            prompt_version=digest_mod.PROMPT_VERSION,
            article_count=article_count,
            source_hash=shash,
            window_start=window_start,
            window_end=window_end,
        )
        return "digested"
    except NarrateError as exc:
        # 검증 실패(빈 요약·투자권유·환각 id 등) — 기존 digest 유지, benign skip.
        logger.info("news digest skipped for %s: %s", ticker, exc)
        return "skipped"
    except AnthropicError as exc:
        logger.info("news digest LLM failed for %s: %s", ticker, exc)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - digest 오류가 수집 사이클을 막지 않는다
        logger.warning("news digest error for %s: %s", ticker, exc)
        return "failed"


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
    digest_client: AnthropicJsonClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """1회 수집 사이클. due 종목 배치를 순회하며 stock_news 적재 + (옵션) digest 갱신.

    pool 은 backend DB 풀. 신규 기사가 들어온 종목만(dirty) digest 를 재요약하고,
    동일 기사집합/하한 간격은 skip 한다. digest 실패는 수집을 막지 않는다(fail-safe).
    """
    now = now or datetime.now(timezone.utc)
    client = client or _default_client(settings)
    # digest 클라이언트: 주입(테스트) > 기본(NEWS_LLM_ENABLED+키). None 이면 digest 비활성(폴백).
    dclient = digest_client if digest_client is not None else _default_digest_client(settings)

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
    digest_counts: Counter[str] = Counter()
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
                inserted = await insert_news_batch(
                    connection,
                    stock_id=int(stock["id"]),
                    ticker=stock["ticker"],
                    items=items,
                )
            total_inserted += inserted
            # dirty-check: 신규 기사가 있는 종목만 digest 재요약(비용 통제).
            if dclient is not None and inserted:
                status = await _maybe_generate_digest(
                    connection, dclient, settings, stock, now=now
                )
                digest_counts[status] += 1

    summary: dict[str, Any] = {
        "due": len(due),
        "processed": processed,
        "inserted": total_inserted,
    }
    if dclient is not None:
        summary["digest"] = {
            "digested": digest_counts.get("digested", 0),
            "skipped": digest_counts.get("skipped", 0),
            "failed": digest_counts.get("failed", 0),
        }
    return summary


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
