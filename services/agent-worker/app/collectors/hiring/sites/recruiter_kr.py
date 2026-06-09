"""
recruiter_kr.py
recruiter.co.kr 위탁 플랫폼 공통 파서

대상 기업 및 URL:
    HL만도          https://hlcompany.recruiter.co.kr/career/job
    셀트리온         https://celltrion.recruiter.co.kr/career/jobs
    유한양행         https://yuhan.recruiter.co.kr/career/apply

recruiter.co.kr 는 Selenium 으로 렌더링 후 파싱.
HTML 셀렉터는 플랫폼 공통 클래스 기반 (사이트 구조 변경 시 수정 필요).
"""

from __future__ import annotations

import logging
import time

from .base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

# 기업명 → (URL, source_label)
RECRUITER_KR_COMPANIES: dict[str, tuple[str, str]] = {
    "HL만도":   ("https://hlcompany.recruiter.co.kr/career/job",    "RECRUITER_KR_HLMANDO"),
    "셀트리온":  ("https://celltrion.recruiter.co.kr/career/jobs",   "RECRUITER_KR_CELLTRION"),
    "유한양행":  ("https://yuhan.recruiter.co.kr/career/apply",      "RECRUITER_KR_YUHAN"),
}


class RecruiterKrCrawler(BaseSiteCrawler):
    """recruiter.co.kr 플랫폼 위탁 기업 공통 크롤러."""

    source_label = "RECRUITER_KR"   # 기업별로 오버라이드됨

    def crawl(self, company_name: str) -> list[dict]:
        if company_name not in RECRUITER_KR_COMPANIES:
            return []

        target_url, label = RECRUITER_KR_COMPANIES[company_name]
        self.source_label = label
        return self._crawl_url(company_name, target_url)

    def _crawl_url(self, company_name: str, url: str) -> list[dict]:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        self._safe_get(url)

        # recruiter.co.kr 는 SPA → JS 렌더링 대기
        try:
            self._wait_for(
                By.CSS_SELECTOR,
                ".career-list li, .job-list-item, [class*='job-item'], [class*='career-item']",
                timeout=8,
            )
        except TimeoutException:
            logger.info("ℹ️  recruiter.co.kr [%s]: 공고 없음 또는 로딩 실패", company_name)
            return []

        time.sleep(1.5)
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        jobs: list[dict] = []

        # recruiter.co.kr 공통 셀렉터 시도 (여러 버전 대응)
        items = (
            soup.select(".career-list li")
            or soup.select(".job-list-item")
            or soup.select("[class*='job-item']")
            or soup.select("[class*='career-item']")
            or soup.select("ul.list > li")
        )

        base = "https://recruiter.co.kr"
        for item in items:
            try:
                # 직무명
                title_el = item.select_one("a .title, a strong, .job-title, h3, h4")
                if not title_el:
                    # fallback: 텍스트 있는 a 태그
                    title_el = item.find("a")
                if not title_el or not title_el.get_text(strip=True):
                    continue
                title = title_el.get_text(strip=True)

                # URL
                link_el = item.find("a") if title_el.name != "a" else title_el
                href = (link_el.get("href", "") if link_el else "")
                job_url = self.normalize_url(href, url)

                # 마감일
                date_el = item.select_one(".date, .deadline, [class*='date'], time")
                deadline = date_el.get_text(strip=True) if date_el else None

                # 직무 설명
                desc_el = item.select_one(".desc, .summary, p")
                desc = desc_el.get_text(strip=True) if desc_el else None

                jobs.append(self._make_record(
                    company_name, title, job_url,
                    job_description=desc,
                    closing_date=deadline,
                ))
            except Exception as exc:
                logger.debug("recruiter.co.kr 파싱 오류 [%s]: %s", company_name, exc)

        return jobs
