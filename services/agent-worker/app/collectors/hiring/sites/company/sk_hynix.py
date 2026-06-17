"""
sk_hynix.py
SK하이닉스 공식 채용 사이트 크롤러
URL: https://talent.skhynix.com/hub/ko/apply/job  (채용 공고 목록)

SPA. 채용중(.recruit-lnb-item.ing) 공고의 '지원 직무' 가 본문에 렌더되며, 각 직무는
/hub/ko/job/introduce?id=N 상세 링크를 가진다. 이 지원직무 링크를 공고 1건으로 수집한다.
구 /hub/ko/home·api/v1/jobs 엔드포인트는 폐기(#175/#233).
"""

from __future__ import annotations

import logging
import time

from ..base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

_BASE = "https://talent.skhynix.com"
_LIST = f"{_BASE}/hub/ko/apply/job"
# 공고(지원 직무) 상세 링크 패턴: /hub/ko/job/introduce?id=N (?id= 없는 네비 링크는 제외)
_JOB_HREF = "/hub/ko/job/introduce?id="


class SKHynixCrawler(BaseSiteCrawler):
    source_label = "SK_HYNIX_CAREERS"

    def crawl(self, company_name: str) -> list:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        self._safe_get(_LIST, wait_sec=2)

        try:
            # 채용중 공고의 지원직무 링크가 있으면 그걸로, 없으면(비수기) lnb 골격으로 렌더 완료 판정
            self._wait_for(
                By.CSS_SELECTOR,
                f"a[href*='{_JOB_HREF}'], .recruit-lnb-item",
                timeout=10,
            )
        except TimeoutException:
            logger.info("ℹ️  SK하이닉스: 공고 목록 로딩 실패")
            return []

        time.sleep(1.5)  # 비동기 데이터 렌더링 안정성을 위해 소폭 확장
        raw_html = self.driver.page_source
        soup = BeautifulSoup(raw_html, "html.parser")
        return self._parse_sk_hynix(company_name, soup, raw_html)

    def _parse_sk_hynix(self, company_name: str, soup, raw_html: str | None = None) -> list:
        """SK하이닉스 채용관(/hub/ko/apply/job) 지원직무 목록 파싱.

        채용중(.recruit-lnb-item.ing) 공고의 '지원 직무' 가 본문에 ``.list-item > a`` 로 렌더되며
        각 a 의 href 는 ``/hub/ko/job/introduce?id=N`` (안정적 상세 URL). 모집 예정(coming-soon)
        공고는 본문 introduce 링크가 렌더되지 않아 자연히 제외된다. 직무명은 ``.title`` 텍스트.
        각 건을 CollectorResult 로 감싸 raw_payload·source_label 을 보존(4b reparse 호환).
        공고가 없으면 빈 리스트를 반환한다.
        """
        from ...base_collector import CollectorResult

        jobs: list = []
        seen: set[str] = set()
        for a in soup.select(f"a[href*='{_JOB_HREF}']"):
            try:
                title_el = a.select_one(".title")
                title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
                if not title:
                    continue
                url = self.normalize_url(a.get("href", ""), _BASE)
                if url in seen:  # 동일 직무 링크 중복 방지
                    continue
                seen.add(url)
                jobs.append(
                    CollectorResult(
                        data=self._make_record(company_name, title, url),
                        raw_payload=raw_html,
                        source_label=self.source_label,
                    )
                )
            except Exception as exc:
                logger.debug("SK하이닉스 introduce 파싱 오류: %s", exc)

        return jobs
