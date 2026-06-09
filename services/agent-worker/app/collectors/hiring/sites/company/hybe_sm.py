"""
hybe_sm.py
HYBE / SM엔터테인먼트 공식 채용 사이트 크롤러

    HYBE  : https://careers.hybecorp.com/ko/home
    SM엔터 : https://recruit.smentertainment.com/ko/home

두 사이트 모두 SPA(React 계열). Selenium 기반 파싱.
"""

from __future__ import annotations

import logging
import time

from ..base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

# ── URL 상수 ──────────────────────────────────────────────────────────────────
_HYBE_BASE = "https://careers.hybecorp.com"
_HYBE_HOME = f"{_HYBE_BASE}/ko/home"
_HYBE_JOBS = f"{_HYBE_BASE}/ko/jobs"

_SM_BASE = "https://recruit.smentertainment.com"
_SM_HOME = f"{_SM_BASE}/ko/home"
_SM_JOBS = f"{_SM_BASE}/ko/jobs"

# 공통 CSS 후보 패턴 (두 사이트에서 동일하게 시도)
_ITEM_SELECTORS = [
    "[class*='JobCard']",
    "[class*='job-card']",
    "[class*='job-item']",
    "[class*='JobItem']",
    ".list-item",
    "ul.jobs li",
    ".careers-list li",
]


def _css_candidates() -> str:
    return ", ".join(_ITEM_SELECTORS)


class _SPACrawlerMixin:
    """Selenium SPA 크롤러 공통 로직 (BaseSiteCrawler 와 함께 사용)."""

    def _crawl_spa(
        self,
        company_name: str,
        home_url: str,
        jobs_url: str,
        base_url: str,
    ) -> list[dict]:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        # 직접 공고 목록 페이지 접근 시도
        self._safe_get(jobs_url, wait_sec=2)

        try:
            self._wait_for(By.CSS_SELECTOR, _css_candidates(), timeout=8)
        except TimeoutException:
            # 홈 페이지에서 재시도
            self._safe_get(home_url, wait_sec=2)
            try:
                self._wait_for(By.CSS_SELECTOR, _css_candidates(), timeout=8)
            except TimeoutException:
                logger.info("ℹ️  %s: 공고 목록 로딩 실패", company_name)
                return []

        # 무한 스크롤 대응
        for _ in range(2):
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1.5)

        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        jobs: list[dict] = []

        items = []
        for sel in _ITEM_SELECTORS:
            items = soup.select(sel)
            if items:
                break

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
                    (link_el.get("href", "") if link_el else ""), base_url
                )

                date_el = item.find(
                    lambda tag: tag.name in ("span", "em", "small")
                    and tag.get_text(strip=True)
                    and any(c in tag.get_text() for c in [".", "-", "~"])
                )
                deadline = date_el.get_text(strip=True) if date_el else None

                jobs.append(self._make_record(
                    company_name, title, url, closing_date=deadline
                ))
            except Exception as exc:
                logger.debug("%s 파싱 오류: %s", company_name, exc)

        return jobs


# ── HYBE ─────────────────────────────────────────────────────────────────────
class HybeCrawler(_SPACrawlerMixin, BaseSiteCrawler):
    source_label = "HYBE_CAREERS"

    def crawl(self, company_name: str) -> list[dict]:
        return self._crawl_spa(company_name, _HYBE_HOME, _HYBE_JOBS, _HYBE_BASE)


# ── SM엔터테인먼트 ─────────────────────────────────────────────────────────────
class SMCrawler(_SPACrawlerMixin, BaseSiteCrawler):
    source_label = "SM_CAREERS"

    def crawl(self, company_name: str) -> list[dict]:
        return self._crawl_spa(company_name, _SM_HOME, _SM_JOBS, _SM_BASE)
