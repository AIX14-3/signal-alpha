"""
krafton.py
크래프톤 공식 채용 사이트 크롤러
URL: https://www.krafton.com/careers/jobs/
"""

from __future__ import annotations

import logging
import time

from ..base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

_BASE = "https://www.krafton.com"
_JOBS = f"{_BASE}/careers/jobs/"


class KraftonCrawler(BaseSiteCrawler):
    source_label = "KRAFTON_CAREERS"

    def crawl(self, company_name: str) -> list[dict]:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        self._safe_get(_JOBS, wait_sec=2)

        try:
            self._wait_for(
                By.CSS_SELECTOR,
                ".job-list-item, [class*='JobCard'], [class*='job-card'], .careers-list li",
                timeout=8,
            )
        except TimeoutException:
            logger.info("ℹ️  크래프톤: 공고 목록 로딩 실패")
            return []

        # 무한 스크롤 대응: 하단까지 스크롤
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        jobs: list[dict] = []

        items = (
            soup.select("[class*='JobCard']")
            or soup.select(".job-list-item")
            or soup.select(".careers-list li")
            or soup.select("[class*='job-card']")
        )

        for item in items:
            try:
                link_el = item.find("a")
                title_el = item.find(["h3", "h4", "strong", "p"])
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if not title:
                    continue
                url = self.normalize_url(
                    (link_el.get("href", "") if link_el else ""), _BASE
                )
                jobs.append(self._make_record(company_name, title, url))
            except Exception as exc:
                logger.debug("크래프톤 파싱 오류: %s", exc)

        return jobs
