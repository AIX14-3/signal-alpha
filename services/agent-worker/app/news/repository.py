"""stock_news 적재 + due 판정 조회 (backend DB 풀 전용).

guard 와 달리 종목 단위 fan-out 이라, 활성 종목 목록과 종목별 최신 수집시각을
읽어 데몬이 "오래된 종목부터" 라운드로빈으로 갱신한다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.news.collector import NewsItem


async def list_active_stocks(pool: Any) -> list[dict[str, Any]]:
    """활성 종목(id/ticker/name). backend DB 의 stocks 발행 사본에서 읽는다."""
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT id, ticker, name FROM stocks WHERE is_active = TRUE ORDER BY id"
        )
    return [dict(row) for row in rows]


async def last_collected_map(pool: Any) -> dict[int, datetime]:
    """종목별 최신 stock_news.collected_at — due 판정용(미수집 종목은 키 없음)."""
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT stock_id, MAX(collected_at) AS last_collected"
            " FROM stock_news GROUP BY stock_id"
        )
    return {int(row["stock_id"]): row["last_collected"] for row in rows}


async def insert_news_batch(
    connection: Any,
    *,
    stock_id: int,
    ticker: str,
    items: list[NewsItem],
) -> int:
    """뉴스 배치를 멱등 적재. (stock_id, article_hash) 중복은 건너뛴다. 신규 건수 반환."""
    inserted = 0
    for item in items:
        event_id = await connection.fetchval(
            "INSERT INTO stock_news"
            " (stock_id, ticker, article_hash, title, summary, url, press, source, published_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)"
            " ON CONFLICT (stock_id, article_hash) DO NOTHING"
            " RETURNING id",
            stock_id,
            ticker,
            item.article_hash,
            item.title,
            item.summary,
            item.url,
            None,  # press — v1 미수집(네이버 뉴스 검색은 언론사명을 직접 주지 않음)
            "NAVER_NEWS",
            item.published_at,
        )
        if event_id is not None:
            inserted += 1
    return inserted


async def list_recent_articles(
    connection: Any, *, stock_id: int, limit: int
) -> list[dict[str, Any]]:
    """digest 후보용 — 종목의 최신 stock_news 를 최신순으로 최대 limit 개. (관련도 필터는 selector 담당.)"""
    rows = await connection.fetch(
        "SELECT article_hash, title, summary, published_at"
        " FROM stock_news WHERE stock_id = $1"
        " ORDER BY published_at DESC NULLS LAST, collected_at DESC"
        " LIMIT $2",
        stock_id,
        max(1, limit),
    )
    return [dict(row) for row in rows]


async def get_digest_meta(connection: Any, stock_id: int) -> dict[str, Any] | None:
    """기존 digest 의 멱등/간격 판정 메타(source_hash, generated_at). 없으면 None."""
    row = await connection.fetchrow(
        "SELECT source_hash, generated_at FROM stock_news_digest WHERE stock_id = $1",
        stock_id,
    )
    return dict(row) if row is not None else None


async def upsert_digest(
    connection: Any,
    *,
    stock_id: int,
    ticker: str,
    digest_text: str,
    model: str,
    prompt_version: str,
    article_count: int,
    source_hash: str,
    window_start: datetime | None,
    window_end: datetime | None,
) -> None:
    """종목 digest 를 멱등 UPSERT(stock_id 1:1). generated_at 은 갱신 시각으로 재설정."""
    await connection.execute(
        "INSERT INTO stock_news_digest"
        " (stock_id, ticker, digest_text, model, prompt_version, article_count,"
        "  source_hash, source_window_start, source_window_end, generated_at)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())"
        " ON CONFLICT (stock_id) DO UPDATE SET"
        "  ticker = EXCLUDED.ticker,"
        "  digest_text = EXCLUDED.digest_text,"
        "  model = EXCLUDED.model,"
        "  prompt_version = EXCLUDED.prompt_version,"
        "  article_count = EXCLUDED.article_count,"
        "  source_hash = EXCLUDED.source_hash,"
        "  source_window_start = EXCLUDED.source_window_start,"
        "  source_window_end = EXCLUDED.source_window_end,"
        "  generated_at = now()",
        stock_id,
        ticker,
        digest_text,
        model,
        prompt_version,
        article_count,
        source_hash,
        window_start,
        window_end,
    )
