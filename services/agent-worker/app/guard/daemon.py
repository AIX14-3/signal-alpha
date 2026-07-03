"""지정학 리스크 감시 데몬 — 수집 → dedupe → LLM 판정 → gate 결정.

drain/price 데몬과 동일한 supervise(advisory lock)/run(생존 루프) 패턴.
수집·LLM 실패 시 상태를 바꾸지 않는다(fail-safe: 기존 차단 상태 유지).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from app.guard.gate import apply_judgment
from app.guard.gdelt import GuardArticle, fetch_gdelt_articles
from app.guard.judge import judge_articles

logger = logging.getLogger("guard_daemon")

_GUARD_ADVISORY_LOCK_KEY = 0x47554152  # "GUAR" — price(0x50524943)/drain(0x5144524E)과 상이
_RESTART_DELAY_SEC = 60.0


class GuardRuntimeStatus:
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


def _default_llm_factory(settings: Any) -> Any:
    from app.clients.gemini_client import GeminiJsonClient

    return GeminiJsonClient(
        api_key=settings.gemini_api_key or None,
        model=settings.guard_llm_model or None,
        temperature=0.0,
        timeout=settings.guard_llm_timeout_seconds,
    )


async def _default_fetch(settings: Any) -> list[GuardArticle]:
    return await fetch_gdelt_articles(
        keywords=list(settings.guard_keywords),
        max_records=settings.guard_news_max_articles,
        timeout_seconds=settings.guard_llm_timeout_seconds,
    )


async def run_guard_cycle(
    pool: Any,
    settings: Any,
    *,
    fetch_articles: Callable[[Any], Any] | None = None,
    llm_factory: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """1회 감시 사이클. pool 은 backend DB 풀(guard_* 소유 DB)이어야 한다."""
    articles = await (fetch_articles or _default_fetch)(settings)
    if not articles:
        return {"collected": 0, "new": 0, "action": "none"}

    hashes = [article.article_hash for article in articles]
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT article_hash FROM guard_news_events WHERE article_hash = ANY($1::text[])",
            hashes,
        )
    known = {row["article_hash"] for row in rows}
    fresh = [article for article in articles if article.article_hash not in known]
    if not fresh:
        return {"collected": len(articles), "new": 0, "action": "none"}

    # dedupe 후 새 기사 묶음당 LLM 1회 — 비용 상한은 폴링 주기 × 배치 1콜.
    judgment = await judge_articles((llm_factory or _default_llm_factory)(settings), fresh)

    async with pool.acquire() as connection:
        async with connection.transaction():
            first_event_id: int | None = None
            for article in fresh:
                event_id = await connection.fetchval(
                    "INSERT INTO guard_news_events"
                    " (source, article_hash, title, url, published_at, severity,"
                    "  is_geopolitical_risk, direction, summary, regions, affected_themes,"
                    "  confidence, prompt_version)"
                    " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)"
                    " ON CONFLICT (article_hash) DO NOTHING"
                    " RETURNING id",
                    article.source,
                    article.article_hash,
                    article.title,
                    article.url,
                    article.published_at,
                    judgment.severity,
                    judgment.is_geopolitical_risk,
                    judgment.direction,
                    judgment.summary,
                    judgment.regions,
                    judgment.affected_themes,
                    judgment.confidence,
                    judgment.prompt_version,
                )
                if first_event_id is None and event_id is not None:
                    first_event_id = int(event_id)
            decision = await apply_judgment(
                connection, settings, judgment, news_event_id=first_event_id
            )

    return {
        "collected": len(articles),
        "new": len(fresh),
        "severity": judgment.severity,
        "direction": judgment.direction,
        **decision,
    }


async def run_guard_daemon(
    pool: Any,
    settings: Any,
    *,
    runtime_status: GuardRuntimeStatus | None = None,
    fetch_articles: Callable[[Any], Any] | None = None,
    llm_factory: Callable[[Any], Any] | None = None,
) -> None:
    try:
        while True:
            try:
                if runtime_status is not None:
                    runtime_status.mark_started()
                summary = await run_guard_cycle(
                    pool, settings, fetch_articles=fetch_articles, llm_factory=llm_factory
                )
                if runtime_status is not None:
                    runtime_status.mark_cycle(summary)
                if summary.get("new"):
                    logger.info("guard cycle completed: %s", summary)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 데몬은 사이클 실패에도 생존(상태 무변경)
                if runtime_status is not None:
                    runtime_status.mark_error(exc)
                logger.exception("guard cycle failed; state unchanged, retrying next interval")
            await asyncio.sleep(settings.guard_poll_interval_sec)
    except asyncio.CancelledError:
        logger.info("guard daemon cancelled")
        raise


async def supervise_guard_daemon(
    pool: Any,
    settings: Any,
    *,
    runtime_status: GuardRuntimeStatus | None = None,
) -> None:
    """advisory lock 으로 단일 기동 보장 + 비정상 종료 시 재기동."""
    while True:
        try:
            async with pool.acquire() as lock_conn:
                locked = await lock_conn.fetchval(
                    "SELECT pg_try_advisory_lock($1)", _GUARD_ADVISORY_LOCK_KEY
                )
                if not locked:
                    logger.warning(
                        "guard daemon lock is held elsewhere; retrying in %.0fs",
                        _RESTART_DELAY_SEC,
                    )
                else:
                    await run_guard_daemon(pool, settings, runtime_status=runtime_status)
                    logger.error("guard daemon exited unexpectedly; restarting")
        except asyncio.CancelledError:
            logger.info("guard daemon supervisor cancelled")
            raise
        except Exception:  # noqa: BLE001 - supervisor 는 크래시 후 재시도
            logger.exception("guard daemon crashed; retrying in %.0fs", _RESTART_DELAY_SEC)
        await asyncio.sleep(_RESTART_DELAY_SEC)
