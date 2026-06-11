"""
sk_hynix.py
SK하이닉스 공식 채용 사이트 크롤러
URL: https://talent.skhynix.com/hub/ko/home

SPA(Vue 계열 추정). 직무 목록은 API 또는 동적 렌더링.
DevTools 에서 JSON API 를 찾지 못할 경우 Selenium full-page 파싱으로 폴백.
"""

from __future__ import annotations

import logging
import time

from ..base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

_BASE = "https://talent.skhynix.com"
_HOME = f"{_BASE}/hub/ko/home"
# 관찰된 내부 API 패턴 (변경 가능) — 실패 시 Selenium 파싱으로 폴백
_API_LIST = f"{_BASE}/api/v1/jobs"


class SKHynixCrawler(BaseSiteCrawler):
    source_label = "SK_HYNIX_CAREERS"

    def crawl(self, company_name: str) -> list[dict]:
        """API 시도 → 실패 시 Selenium 파싱."""
        jobs = self._try_api(company_name)
        if not jobs and self.driver:
            jobs = self._selenium_parse(company_name)
        return jobs

    # ── 1) API 시도 ──────────────────────────────────────────────────────────────
    def _try_api(self, company_name: str) -> list[dict]:
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
            "Accept": "application/json",
            "Referer": _HOME,
        }
        params = {"language": "ko", "page": 1, "size": 100}

        try:
            resp = requests.get(_API_LIST, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            items = (
                data.get("content")
                or data.get("jobs")
                or data.get("list")
                or data.get("data")
                or []
            )
            if not items:
                return []

            jobs: list[dict] = []
            for item in items:
                title = (
                    item.get("jobTitle") or item.get("title") or item.get("name") or ""
                )
                if not title:
                    continue
                job_id = item.get("jobId") or item.get("id") or ""
                url = item.get("applyUrl") or (
                    f"{_BASE}/hub/ko/jobs/{job_id}" if job_id else _HOME
                )
                deadline = item.get("closingDate") or item.get("endDate")
                desc = item.get("jobDescription") or item.get("description")
                jobs.append(self._make_record(
                    company_name, title, url,
                    job_description=desc, closing_date=deadline,
                ))
            return jobs

        except Exception as exc:
            logger.debug("SK하이닉스 API 실패: %s", exc)
            return []

    # ── 2) Selenium 전체 파싱 ────────────────────────────────────────────────────
    def _selenium_parse(self, company_name: str) -> list[dict]:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        self._safe_get(_HOME, wait_sec=2)

        # '채용공고' 메뉴 링크 클릭 시도
        try:
            job_link = self.driver.find_element(
                By.XPATH, "//a[contains(@href, 'jobs') or contains(text(), '공고') or contains(text(), 'Job')]"
            )
            job_link.click()
            time.sleep(2)
        except Exception:
            pass

        # 공고 목록 로딩 대기
        try:
            self._wait_for(
                By.CSS_SELECTOR,
                "[class*='job'], [class*='Job'], .recruit-item, li.item",
                timeout=8,
            )
        except TimeoutException:
            logger.info("ℹ️  SK하이닉스: Selenium 공고 목록 로딩 실패")
            return []

        # 스크롤 다운 (무한 스크롤 대응)
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        jobs: list[dict] = []

        # 다양한 CSS 패턴 시도
        items = (
            soup.select("[class*='JobCard']")
            or soup.select("[class*='job-item']")
            or soup.select("[class*='recruit']")
            or soup.select("ul.list li")
            or soup.select(".content-list li")
        )

        for item in items:
            try:
                link_el = item.find("a")
                title_el = item.find(["h3", "h4", "strong", "span", "p"])
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if not title or len(title) < 3:
                    continue
                url = self.normalize_url(
                    (link_el.get("href", "") if link_el else ""), _BASE
                )
                jobs.append(self._make_record(company_name, title, url))
            except Exception as exc:
                logger.debug("SK하이닉스 Selenium 파싱 오류: %s", exc)

        return jobs
