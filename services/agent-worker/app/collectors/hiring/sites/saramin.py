"""
saramin.py
사람인 채용공고 크롤러
URL: https://www.saramin.co.kr/zf_user/search/recruit?searchword={company}
"""

from __future__ import annotations

import logging
import time

from .base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

_BASE = "https://www.saramin.co.kr"
_SEARCH = f"{_BASE}/zf_user/search/recruit"


class SaraminCrawler(BaseSiteCrawler):
    source_label = "SARAMIN"

    def crawl(self, company_name: str) -> list[dict]:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        self._safe_get(f"{_SEARCH}?searchword={company_name}&searchType=search")

        try:
            self._wait_for(By.CLASS_NAME, "item_recruit", timeout=5)
        except TimeoutException:
            logger.info("ℹ️  사람인 [%s]: 공고 없음", company_name)
            return []

        time.sleep(1)
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        jobs: list[dict] = []

        for elem in soup.find_all("div", class_="item_recruit"):
            try:
                link_el = elem.find("a", class_="item_title")
                if not link_el:
                    continue
                title = link_el.get_text(strip=True)
                url = self.normalize_url(link_el.get("href", ""), _BASE)

                corp_el = elem.find("strong", class_="corp_name") \
                    or elem.find("span", class_="corp_name")
                corp = corp_el.get_text(strip=True) if corp_el else company_name

                date_el = elem.find("span", class_="date")
                deadline = date_el.get_text(strip=True) if date_el else None

                desc_el = elem.find("div", class_="job_sector") \
                    or elem.find("span", class_="job_summary")
                desc = desc_el.get_text(strip=True) if desc_el else None

                jobs.append(self._make_record(
                    corp, title, url,
                    job_description=desc,
                    closing_date=deadline,
                ))
            except Exception as exc:
                logger.debug("사람인 파싱 오류: %s", exc)

        return jobs
