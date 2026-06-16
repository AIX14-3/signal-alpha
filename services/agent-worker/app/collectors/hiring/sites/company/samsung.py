"""
samsung.py
삼성전자 공식 채용 사이트 크롤러
URL: https://careers.samsung.com/kr

Samsung Careers 는 SPA(React) 이며 내부 REST API 를 사용.
DevTools Network 탭에서 관찰된 API 엔드포인트를 직접 호출 (JSON 파싱).
Selenium 없이 requests 로 처리 가능.
"""

from __future__ import annotations

import logging

from ..base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

_BASE = "https://careers.samsung.com"
# Samsung Careers 공개 검색 API (DevTools 관찰 기반, 변경 가능)
_API = (
    f"{_BASE}/kr/restful/jobSearchResults.json"
    "?jobCategory=&jobSubCategory=&country=KR&language=ko"
    "&pageIndex={page}&pageSize=20&sortBy=recent"
)


class SamsungCrawler(BaseSiteCrawler):
    source_label = "SAMSUNG_CAREERS"

    def crawl(self, company_name: str) -> list[dict]:
        """Samsung Careers JSON API 호출 (최대 3페이지)."""
        from ..http import get as http_get

        jobs: list[dict] = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
            "Referer": f"{_BASE}/kr/",
        }

        for page in range(1, 4):  # 최대 3페이지
            try:
                resp = http_get(_API.format(page=page), headers=headers)
                data = resp.json()

                items = (
                    data.get("jobSearchResults")
                    or data.get("jobs")
                    or data.get("results")
                    or []
                )
                if not items:
                    break

                for item in items:
                    title = (
                        item.get("jobTitle") or item.get("title") or item.get("name") or ""
                    )
                    if not title:
                        continue
                    job_id = item.get("jobId") or item.get("id") or ""
                    url = (
                        item.get("applyUrl")
                        or item.get("url")
                        or f"{_BASE}/kr/job/{job_id}" if job_id else _BASE
                    )
                    deadline = item.get("closingDate") or item.get("deadline")
                    desc = item.get("jobSummary") or item.get("description")

                    jobs.append(self._make_record(
                        company_name, title, url,
                        job_description=desc,
                        closing_date=deadline,
                    ))

            except Exception as exc:
                logger.warning("삼성전자 API 오류 (page=%d): %s", page, exc)
                break

        if not jobs:
            logger.warning(
                "⚠️  삼성전자 API 응답 비어있음 — API 스펙 변경 가능성. "
                "DevTools 에서 재확인 후 _API 상수 업데이트 필요"
            )
        return jobs
