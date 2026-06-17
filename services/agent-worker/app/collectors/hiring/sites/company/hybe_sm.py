"""
hybe_sm.py
HYBE / SM엔터테인먼트 공식 채용 사이트 크롤러

    HYBE  : https://careers.hybecorp.com/ko/career
    SM엔터 : https://recruit.smentertainment.com/ko/sm-apply

두 사이트 모두 GreetingHR ATS 임베드(동일 구조) → 파서 1개 공유. 공고는 ``a[href*="/ko/o/<id>"]``
(opening) 링크로 렌더되며 제목은 ``[class*="OpeningListItemTitle"]``. 구 /ko/jobs 는 404(#175/#241·#242).
"""

from __future__ import annotations

import logging
import time

from ..base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

# ── URL 상수 ──────────────────────────────────────────────────────────────────
_HYBE_BASE = "https://careers.hybecorp.com"
_HYBE_JOBS = f"{_HYBE_BASE}/ko/career"

_SM_BASE = "https://recruit.smentertainment.com"
_SM_JOBS = f"{_SM_BASE}/ko/sm-apply"

# GreetingHR 공고(opening) 상세 링크 패턴: /ko/o/<id>
_OPENING_HREF = "/ko/o/"


class _GreetingHRCrawlerMixin:
    """GreetingHR 임베드 채용관 공통 로직 (BaseSiteCrawler 와 함께 사용)."""

    def _crawl_greetinghr(self, company_name: str, jobs_url: str, base_url: str) -> list:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        self._safe_get(jobs_url, wait_sec=2)

        try:
            # 공고(opening) 링크가 뜨면 그걸로, 없으면 opening 컨테이너로 렌더 완료 판정
            self._wait_for(
                By.CSS_SELECTOR,
                f"a[href*='{_OPENING_HREF}'], [class*='OpeningItemContainer']",
                timeout=10,
            )
        except TimeoutException:
            logger.info("ℹ️  %s: 공고 목록 로딩 실패", company_name)
            return []

        # 지연 로딩(lazy) 대응: 하단까지 스크롤
        for _ in range(2):
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1.5)

        raw_html = self.driver.page_source
        soup = BeautifulSoup(raw_html, "html.parser")
        return self._parse_greetinghr(company_name, soup, base_url, raw_html)

    def _parse_greetinghr(
        self, company_name: str, soup, base_url: str, raw_html: str | None = None
    ) -> list:
        """GreetingHR opening 목록 파싱.

        공고는 ``a[href*="/ko/o/<id>"]`` (opening). 제목은 anchor 내부
        ``[class*="OpeningListItemTitle"]`` (styled-components 해시 클래스라 부분매칭), 없으면 anchor 텍스트.
        상세 URL 은 ``{base}/ko/o/<id>``. 각 건을 CollectorResult 로 감싸 raw_payload·source_label 보존.
        공고가 없으면 빈 리스트를 반환한다.
        """
        from ...base_collector import CollectorResult

        jobs: list = []
        seen: set[str] = set()
        for a in soup.select(f"a[href*='{_OPENING_HREF}']"):
            try:
                title_el = a.select_one("[class*='OpeningListItemTitle']")
                title = (title_el or a).get_text(strip=True)
                if not title:
                    continue
                url = self.normalize_url(a.get("href", ""), base_url)
                if url in seen:
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
                logger.debug("%s GreetingHR 파싱 오류: %s", company_name, exc)

        return jobs


# ── HYBE ─────────────────────────────────────────────────────────────────────
class HybeCrawler(_GreetingHRCrawlerMixin, BaseSiteCrawler):
    source_label = "HYBE_CAREERS"

    def crawl(self, company_name: str) -> list:
        return self._crawl_greetinghr(company_name, _HYBE_JOBS, _HYBE_BASE)


# ── SM엔터테인먼트 ─────────────────────────────────────────────────────────────
class SMCrawler(_GreetingHRCrawlerMixin, BaseSiteCrawler):
    source_label = "SM_CAREERS"

    def crawl(self, company_name: str) -> list:
        return self._crawl_greetinghr(company_name, _SM_JOBS, _SM_BASE)
