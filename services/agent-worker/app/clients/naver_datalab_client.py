from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


class NaverDataLabError(RuntimeError):
    pass


class NaverDataLabRecord:
    __slots__ = ("keyword_group", "keyword", "period", "ratio", "raw_data")

    def __init__(
        self,
        *,
        keyword_group: str,
        keyword: str,
        period: str,
        ratio: float,
        raw_data: dict[str, Any],
    ) -> None:
        self.keyword_group = keyword_group
        self.keyword = keyword
        self.period = period
        self.ratio = ratio
        self.raw_data = raw_data


class NaverDataLabClient:
    """Client for Naver DataLab search trend API."""

    API_URL = "https://openapi.naver.com/v1/datalab/search"

    MAX_RETRIES = 3
    RETRY_BACKOFF_SECONDS = 1.0

    PERIOD_TYPE_MAP = {
        "date": "daily",
        "week": "weekly",
        "month": "monthly",
    }

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        timeout_seconds: int = 15,
    ) -> None:
        if not client_id or not client_secret:
            raise NaverDataLabError("Naver client_id and client_secret are required.")
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout_seconds = timeout_seconds

    async def search(
        self,
        *,
        keyword_group: str,
        keywords: list[str],
        start_date: str,
        end_date: str,
        time_unit: str = "date",
        device: str = "",
        gender: str = "",
        ages: list[str] | None = None,
    ) -> list[NaverDataLabRecord]:
        """Call the DataLab API once per keyword and return per-keyword records.

        Naver aggregates every term inside one keywordGroup. To preserve
        independent trend series, each keyword is sent as its own one-keyword
        keywordGroup while the stored keyword_group remains the category name.
        """
        records: list[NaverDataLabRecord] = []
        for keyword in keywords:
            body = {
                "startDate": start_date,
                "endDate": end_date,
                "timeUnit": time_unit,
                "keywordGroups": [
                    {"groupName": keyword, "keywords": [keyword]}
                ],
                "device": device,
                "gender": gender,
                "ages": ages or [],
            }
            response = await asyncio.to_thread(self._post_json, body)
            records.extend(
                self._parse_response(
                    response,
                    keyword_group=keyword_group,
                    keyword=keyword,
                    time_unit=time_unit,
                )
            )
        return records

    async def search_grouped(
        self,
        *,
        keyword_group: str,
        keywords: list[str],
        start_date: str,
        end_date: str,
        time_unit: str = "date",
        device: str = "",
        gender: str = "",
        ages: list[str] | None = None,
    ) -> list[NaverDataLabRecord]:
        """Call the DataLab API with all keywords in one aggregated group."""
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "timeUnit": time_unit,
            "keywordGroups": [
                {"groupName": keyword_group, "keywords": keywords}
            ],
            "device": device,
            "gender": gender,
            "ages": ages or [],
        }
        response = await asyncio.to_thread(self._post_json, body)
        return self._parse_response(response, keyword_group=keyword_group, keyword=None, time_unit=time_unit)

    def _post_json(self, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.API_URL,
            data=data,
            headers={
                "X-Naver-Client-Id": self._client_id,
                "X-Naver-Client-Secret": self._client_secret,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        # The DataLab API intermittently stalls on a request (~1 in 15); retry
        # transient network/timeout failures so one stall does not abort a run.
        last_error: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                with urlopen(request, timeout=self._timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (TimeoutError, URLError) as exc:
                last_error = exc
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise NaverDataLabError(
            f"Naver DataLab request failed after {self.MAX_RETRIES} attempts: {last_error}"
        )

    def _parse_response(
        self,
        response: dict[str, Any],
        *,
        keyword_group: str,
        keyword: str | None,
        time_unit: str,
    ) -> list[NaverDataLabRecord]:
        records: list[NaverDataLabRecord] = []
        results = response.get("results", [])
        for result in results:
            group_name = keyword_group
            keywords = result.get("keywords", [])
            representative_keyword = keyword or (keywords[0] if keywords else result.get("title", keyword_group))
            data_points = result.get("data", [])
            for point in data_points:
                period = point.get("period", "")
                ratio = float(point.get("ratio", 0.0))
                records.append(
                    NaverDataLabRecord(
                        keyword_group=group_name,
                        keyword=representative_keyword,
                        period=period,
                        ratio=ratio,
                        raw_data={"title": result.get("title"), "point": point},
                    )
                )
        return records

    @classmethod
    def time_unit_to_period_type(cls, time_unit: str) -> str:
        return cls.PERIOD_TYPE_MAP.get(time_unit, "daily")


def _default_start_date() -> str:
    from datetime import date, timedelta

    return (date.today() - timedelta(days=1)).isoformat()


def _default_end_date() -> str:
    from datetime import date, timedelta

    return (date.today() - timedelta(days=1)).isoformat()
