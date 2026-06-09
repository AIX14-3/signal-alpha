"""
kakao.py
카카오 공식 채용 사이트 크롤러
URL: https://careers.kakao.com/jobs?company=KAKAO&part=TECHNOLOGY

카카오 채용 사이트는 SPA. URL 파라미터로 필터링 가능.
company=KAKAO 고정, part=TECHNOLOGY 기본.
"""

from __future__ import annotations

import logging
import time

from ..base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

_BASE = "https://careers.kakao.com"
_LIST = f"{_BASE}/jobs"


class KakaoCrawler(BaseSiteCrawler):
    source_label = "KAKAO_CAREERS"

    # 카카오 그룹사 필터 (KAKAO, KAKAOBANK, KAKAOPAY, KAKAOENTERTAINMENT 등)
    _COMPANIES = ["KAKAO"]

    def crawl(self, company_name: str) -> list[dict]:
        """카카오 채용 페이지 Selenium 크롤링."""
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        jobs: list[dict] = []

        for corp_code in self._COMPANIES:
            url = f"{_LIST}?company={corp_code}&employeeType=&keyword="
            self._safe_get(url, wait_sec=2)

            try:
                self._wait_for(
                    By.CSS_SELECTOR,
                    ".list_jobs li, .job-item, [class*='JobItem']",
                    timeout=8,
                )
            except TimeoutException:
                logger.info("ℹ️  카카오 [%s]: 공고 없음", corp_code)
                continue

            time.sleep(1.5)
            soup = BeautifulSoup(self.driver.page_source, "html.parser")

            for item in (
                soup.select(".list_jobs li")
                or soup.select(".job-item")
                or soup.select("[class*='JobItem']")
                or soup.select("ul li")
            ):
                try:
                    link_el = item.find("a")
                    title_el = item.find(["strong", "h3", "h4", "span"])
                    if not title_el:
                        continue
                    title = title_el.get_text(strip=True)
                    if not title or len(title) < 3:
                        continue
                    url_job = self.normalize_url(
                        (link_el.get("href", "") if link_el else ""), _BASE
                    )

                    date_el = item.find(["em", "span"], class_=lambda c: c and "date" in c.lower())
                    deadline = date_el.get_text(strip=True) if date_el else None

                    jobs.append(self._make_record(
                        company_name, title, url_job, closing_date=deadline
                    ))
                except Exception as exc:
                    logger.debug("카카오 파싱 오류: %s", exc)

        return jobs
