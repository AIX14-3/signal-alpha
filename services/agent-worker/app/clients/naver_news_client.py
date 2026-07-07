"""Naver News Search API 클라이언트 — 종목별 뉴스 수집용.

전송층은 app/clients 관례(naver_datalab_client)와 동일하게 의존성 없는 urllib
(스레드 오프로드)로 유지한다. 인증은 DataLab 과 같은 X-Naver-Client-Id/Secret 쌍.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class NaverNewsError(RuntimeError):
    pass


@dataclass(frozen=True)
class NaverNewsItem:
    """Naver 뉴스 검색 결과 1건(원시). HTML 정제·윈도우링은 collector 책임."""

    title: str
    description: str
    url: str | None
    pub_date: str | None  # RFC-2822 문자열(예: "Mon, 06 Jul 2026 09:12:00 +0900")


class NaverNewsClient:
    """Client for the Naver News Search API (openapi.naver.com/v1/search/news)."""

    API_URL = "https://openapi.naver.com/v1/search/news.json"

    MAX_RETRIES = 3
    RETRY_BACKOFF_SECONDS = 1.0

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        timeout_seconds: int = 15,
    ) -> None:
        if not client_id or not client_secret:
            raise NaverNewsError("Naver client_id and client_secret are required.")
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout_seconds = timeout_seconds

    async def search(
        self,
        query: str,
        *,
        display: int = 20,
        sort: str = "date",
    ) -> list[NaverNewsItem]:
        """뉴스 검색 1회. sort='date'(최신순) 기본. display 는 1..100 로 클램프."""
        if not query.strip():
            return []
        response = await asyncio.to_thread(self._get_json, query, display, sort)
        return self._parse(response)

    def _get_json(self, query: str, display: int, sort: str) -> dict[str, Any]:
        params = urlencode(
            {
                "query": query,
                "display": min(max(display, 1), 100),
                "sort": sort,
            }
        )
        request = Request(
            f"{self.API_URL}?{params}",
            headers={
                "X-Naver-Client-Id": self._client_id,
                "X-Naver-Client-Secret": self._client_secret,
            },
        )
        # DataLab 클라이언트와 동일 정책: 4xx(429 포함)는 즉시 실패(쿼터/요청오류라
        # 재시도 낭비), 5xx·네트워크 오류만 선형 백오프 재시도.
        last_error: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                with urlopen(request, timeout=self._timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                if exc.code < 500:
                    raise NaverNewsError(f"Naver News HTTP {exc.code} {exc.reason}") from exc
                last_error = exc
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_BACKOFF_SECONDS * (attempt + 1))
            except (TimeoutError, URLError) as exc:
                last_error = exc
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise NaverNewsError(
            f"Naver News request failed after {self.MAX_RETRIES} attempts: {last_error}"
        )

    def _parse(self, response: dict[str, Any]) -> list[NaverNewsItem]:
        items: list[NaverNewsItem] = []
        for item in response.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            if not isinstance(title, str) or not title.strip():
                continue
            # 원문 링크(originallink) 우선, 없으면 네이버 링크(link).
            url = item.get("originallink") or item.get("link")
            items.append(
                NaverNewsItem(
                    title=title,
                    description=item.get("description") or "",
                    url=url if isinstance(url, str) and url.strip() else None,
                    pub_date=item.get("pubDate") if isinstance(item.get("pubDate"), str) else None,
                )
            )
        return items
