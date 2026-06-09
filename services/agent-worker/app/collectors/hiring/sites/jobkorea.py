"""
jobkorea.py
잡코리아 채용공고 크롤러
URL: https://www.jobkorea.co.kr/Search/?stext={company}&tabType=recruit
"""

from __future__ import annotations

import logging
import time

from .base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

_BASE = "https://www.jobkorea.co.kr"
_SEARCH = f"{_BASE}/Search/"


class JobkoreaCrawler(BaseSiteCrawler):
    source_label = "JOBKOREA"

    def crawl(self, company_name: str) -> list[dict]:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        url = f"{_SEARCH}?stext={company_name}&tabType=recruit"
        self._safe_get(url)

        try:
            self._wait_for(By.CSS_SELECTOR, ".list-post .post-list-info", timeout=5)
        except TimeoutException:
            logger.info("ℹ️  잡코리아 [%s]: 공고 없음", company_name)
            return []

        time.sleep(1)
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        jobs: list[dict] = []

        # 잡코리아 검색 결과: .list-post li 또는 .recruit-info
        for elem in soup.select(".list-post .post-list-info, .recruit-info"):
            try:
                # 직무명
                title_el = elem.select_one(".title, .post-title a, h4 a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)

                # URL
                href = title_el.get("href", "")
                job_url = self.normalize_url(href, _BASE)

                # 기업명
                corp_el = elem.select_one(".name, .corp-name, .company-name")
                corp = corp_el.get_text(strip=True) if corp_el else company_name

                # 마감일
                date_el = elem.select_one(".date, .deadline")
                deadline = date_el.get_text(strip=True) if date_el else None

                # 직무 설명
                desc_el = elem.select_one(".etc, .summary")
                desc = desc_el.get_text(strip=True) if desc_el else None

                jobs.append(self._make_record(
                    corp, title, job_url,
                    job_description=desc,
                    closing_date=deadline,
                ))
            except Exception as exc:
                logger.debug("잡코리아 파싱 오류: %s", exc)

        return jobs
