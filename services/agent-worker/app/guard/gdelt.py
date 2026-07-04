"""GDELT DOC 2.0 뉴스 수집 — 지정학 리스크 감시 전용.

무료·키 불필요·15분 갱신이라 감시 시작점으로 적합. 전송층은 app/clients 관례대로
의존성 없는 urllib(스레드 오프로드)로 유지한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_GDELT_DOC_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"


class GuardCollectError(RuntimeError):
    """GDELT 호출 실패 또는 응답 파싱 불가."""


@dataclass(frozen=True)
class GuardArticle:
    source: str
    article_hash: str
    title: str | None
    url: str | None
    published_at: datetime | None


def compute_article_hash(url: str | None, title: str | None) -> str:
    """중복 제거 키 — url 우선, 없으면 title 기반."""
    basis = (url or "").strip() or (title or "").strip()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def parse_gdelt_articles(payload: Any) -> list[GuardArticle]:
    """GDELT ArtList JSON → GuardArticle 목록(배치 내 hash 중복 제거)."""
    articles: list[GuardArticle] = []
    seen: set[str] = set()
    for item in (payload or {}).get("articles", []) or []:
        if not isinstance(item, dict):
            continue
        url = _to_str(item.get("url"))
        title = _to_str(item.get("title"))
        if not url and not title:
            continue
        digest = compute_article_hash(url, title)
        if digest in seen:
            continue
        seen.add(digest)
        articles.append(
            GuardArticle(
                source="gdelt",
                article_hash=digest,
                title=title,
                url=url,
                published_at=_parse_seendate(item.get("seendate")),
            )
        )
    return articles


async def fetch_gdelt_articles(
    *,
    keywords: list[str],
    max_records: int,
    timeout_seconds: float,
    timespan: str = "1h",
) -> list[GuardArticle]:
    """키워드 OR 질의로 최근 기사 목록을 가져온다. 실패는 GuardCollectError."""
    query = " OR ".join(f'"{keyword.strip()}"' for keyword in keywords if keyword.strip())
    if not query:
        return []
    params = urlencode(
        {
            "query": f"({query})",
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(max(1, max_records)),
            "timespan": timespan,
            "sort": "DateDesc",
        }
    )
    return await asyncio.to_thread(_fetch_sync, f"{_GDELT_DOC_ENDPOINT}?{params}", timeout_seconds)


def _fetch_sync(url: str, timeout_seconds: float) -> list[GuardArticle]:
    request = Request(url, headers={"User-Agent": "signal-alpha-guard/1.0"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise GuardCollectError(f"GDELT fetch failed: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        # GDELT 는 잘못된 질의를 200 + 평문으로 답하기도 한다 — 파싱 실패로 취급.
        raise GuardCollectError(f"GDELT returned non-JSON body: {raw[:120]!r}") from exc
    return parse_gdelt_articles(payload)


def _parse_seendate(value: Any) -> datetime | None:
    # 예: "20260703T114500Z"
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _to_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
