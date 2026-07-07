"""Naver 뉴스 검색 결과 → stock_news 적재용 NewsItem 매핑.

news_event_source.py(인메모리 키워드 헬퍼)의 정제·윈도우·dedup 로직을 참고하되,
저장에 필요한 url/hash 를 보존한다. 방향/점수/요약 판정은 하지 않는다(display-only).
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from app.clients.naver_news_client import NaverNewsClient, NaverNewsItem

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class NewsItem:
    article_hash: str
    title: str
    summary: str | None
    url: str | None
    published_at: datetime | None


def clean_text(text: str | None) -> str:
    """Naver 의 <b> 하이라이트 태그 제거 + HTML 엔티티(&quot; 등) 복원."""
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def compute_article_hash(url: str | None, title: str | None) -> str:
    """중복 제거 키 — url 우선, 없으면 title 기반 sha256."""
    basis = (url or "").strip() or (title or "").strip()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _parse_pub_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    # naive → UTC 로 간주(윈도우 비교 안정화).
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _within_window(published_at: datetime | None, cutoff: datetime) -> bool:
    # 날짜 없는/파싱 실패 항목은 보존(윈도우 밖으로 단정하지 않음).
    if published_at is None:
        return True
    return published_at >= cutoff


async def collect_stock_news(
    client: NaverNewsClient,
    query: str,
    *,
    lookback_days: int,
    max_items: int,
    now: datetime | None = None,
) -> list[NewsItem]:
    """종목명(query)으로 최근 뉴스를 검색해 NewsItem 목록으로 매핑.

    - display 는 윈도우/dedup 로 줄어들 것을 감안해 max_items 의 2배(≤100) 요청.
    - 정제한 제목 기준으로 dedup, lookback 윈도우 필터, max_items 로 캡.
    """
    if not query.strip():
        return []
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)

    raw: list[NaverNewsItem] = await client.search(
        query, display=min(max(max_items * 2, max_items), 100), sort="date"
    )

    items: list[NewsItem] = []
    seen_titles: set[str] = set()
    for entry in raw:
        title = clean_text(entry.title)
        if not title or title in seen_titles:
            continue
        published_at = _parse_pub_date(entry.pub_date)
        if not _within_window(published_at, cutoff):
            continue
        seen_titles.add(title)
        summary = clean_text(entry.description) or None
        items.append(
            NewsItem(
                article_hash=compute_article_hash(entry.url, title),
                title=title,
                summary=summary,
                url=entry.url,
                published_at=published_at,
            )
        )
        if len(items) >= max_items:
            break
    return items
