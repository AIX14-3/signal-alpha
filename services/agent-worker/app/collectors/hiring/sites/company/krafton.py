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

    def crawl(self, company_name: str) -> list:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        self._safe_get(_JOBS, wait_sec=2)

        try:
            # 공고가 있으면 RecruitItem, 없으면 RecruitList 골격으로 렌더 완료 판정
            self._wait_for(
                By.CSS_SELECTOR,
                ".RecruitItem, .RecruitList",
                timeout=8,
            )
        except TimeoutException:
            logger.info("ℹ️  크래프톤: 공고 목록 로딩 실패")
            return []

        # 무한 스크롤 대응: 하단까지 스크롤
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

        raw_html = self.driver.page_source
        soup = BeautifulSoup(raw_html, "html.parser")
        return self._parse_krafton(company_name, soup, raw_html)

    def _parse_krafton(self, company_name: str, soup, raw_html: str | None = None) -> list:
        """크래프톤 채용관(careers/jobs/, SSR) 목록 파싱.

        공고는 ``div.RecruitItem``. 직무명 ``h3.RecruitItemTitle-title``, 상세 링크
        ``a.RecruitItemTitle-link``(``/careers/recruit-detail/?...&job=N``). 직군/고용형태/지역은
        ``span.RecruitItemMetaCategory-text`` 에서 모아 job_description 에 보존. 북마크 앵커
        (``a.RecruitItem-mark``, href="#;")는 직무 링크가 아니므로 쓰지 않는다.
        각 건을 CollectorResult 로 감싸 raw_payload·source_label 을 보존(4b reparse 호환).
        공고가 없으면 빈 리스트를 반환한다.
        """
        from ...base_collector import CollectorResult

        jobs: list = []
        seen: set[str] = set()
        for item in soup.select("div.RecruitItem"):
            try:
                title_el = item.select_one("h3.RecruitItemTitle-title")
                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue

                link_el = item.select_one("a.RecruitItemTitle-link")
                if not link_el:
                    continue
                url = self.normalize_url(link_el.get("href", ""), _BASE)
                if url in seen:
                    continue
                seen.add(url)

                meta = [m.get_text(strip=True) for m in item.select("span.RecruitItemMetaCategory-text")]
                description = " / ".join(m for m in meta if m) or None

                jobs.append(
                    CollectorResult(
                        data=self._make_record(
                            company_name, title, url, job_description=description
                        ),
                        raw_payload=raw_html,
                        source_label=self.source_label,
                    )
                )
            except Exception as exc:
                logger.debug("크래프톤 RecruitItem 파싱 오류: %s", exc)

        return jobs
