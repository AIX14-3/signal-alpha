"""
naver.py
NAVER 공식 채용 사이트 크롤러
URL: https://recruit.navercorp.com
"""

from __future__ import annotations

import logging
from bs4 import BeautifulSoup
from ..base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

_BASE = "https://recruit.navercorp.com"
_LIST_URL = f"{_BASE}/rcrt/list.do"


class NaverCrawler(BaseSiteCrawler):
    source_label = "NAVER_CAREERS"

    def crawl(self, company_name: str) -> list:
        """NAVER 채용 공고 수집. 목록은 SSR(li.card_item)이라 requests 로 충분, 실패 시 Selenium 폴백."""
        from ..http import get as http_get

        # User-Agent는 http_get이 풀에서 로테이션 주입한다.
        headers = {
            "Referer": _BASE,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        }
        params = {"pageNo": 1}

        jobs: list = []
        try:
            # 1차 시도: requests (retry/backoff 적용) — 카드가 SSR 이라 JS 없이도 잡힘
            resp = http_get(_LIST_URL, params=params, headers=headers)
            raw_html = resp.text
            soup = BeautifulSoup(raw_html, "html.parser")
            jobs = self._parse_naver(soup, company_name, raw_html)

            if not jobs and self.driver:
                logger.info("NAVER 1차 파싱 결과 없음 -> Selenium 폴백 전환")
                jobs = self._selenium_fallback(company_name)

        except Exception as exc:
            logger.warning("NAVER 채용 1차 시도 오류: %s", exc)
            if self.driver:
                jobs = self._selenium_fallback(company_name)

        return jobs

    def _selenium_fallback(self, company_name: str) -> list:
        """Selenium 폴백: 브라우저로 목록 페이지를 열어 card_item 렌더 후 수집."""
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        self._safe_get(_LIST_URL)

        try:
            # 공고 카드(li.card_item > a.card_link)가 나타날 때까지 대기. 없으면 빈 목록 골격(card_list)으로 종료 판정
            self._wait_for(By.CSS_SELECTOR, "li.card_item a.card_link, ul.card_list", timeout=12)
        except TimeoutException:
            logger.warning("⚠️ NAVER Selenium 폴백 타임아웃: 공고 카드 요소를 찾을 수 없음")
            return []

        raw_html = self.driver.page_source
        soup = BeautifulSoup(raw_html, "html.parser")
        return self._parse_naver(soup, company_name, raw_html)

    def _parse_naver(self, soup: BeautifulSoup, company_name: str, raw_html: str | None = None) -> list:
        """NAVER 채용관(rcrt/list.do) 카드 목록 파싱.

        공고는 ``li.card_item`` 이며, 상세 링크 <a class="card_link"> 는 href 가 아니라
        ``onclick="show('<annoId>')"`` 로 이동한다(copyUrl 기준 GET 형태:
        ``/rcrt/view.do?annoId=<id>&lang=ko``). 직무명은 ``.card_title``, 마감일은 card_info 의
        모집 기간 종료일(YYYY.MM.DD → YYYY-MM-DD). 마감된 카드(li.card_item.disabled)는 제외.
        각 건을 CollectorResult 로 감싸 raw_payload·source_label 을 보존(4b reparse 호환).
        """
        import re

        from ...base_collector import CollectorResult

        jobs: list = []
        seen: set[str] = set()
        for li in soup.select("li.card_item"):
            try:
                if "disabled" in (li.get("class") or []):  # 마감된 공고 제외
                    continue
                a = li.select_one("a.card_link")
                if not a:
                    continue
                # annoId 는 onclick="show('30004975')" 에서 추출
                m = re.search(r"show\('?(\d+)'?\)", a.get("onclick", ""))
                if not m:
                    continue
                url = f"{_BASE}/rcrt/view.do?annoId={m.group(1)}&lang=ko"
                if url in seen:  # 동일 annoId 중복 방지
                    continue

                title_el = a.select_one(".card_title")
                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue

                # 모집 기간 "2026.06.12 ~ 2026.06.22" → 종료일 YYYY-MM-DD (상시 등 날짜 없으면 None)
                dates = re.findall(r"\d{4}\.\d{2}\.\d{2}", a.get_text())
                deadline = dates[-1].replace(".", "-") if dates else None

                seen.add(url)
                jobs.append(
                    CollectorResult(
                        data=self._make_record(company_name, title, url, closing_date=deadline),
                        raw_payload=raw_html,
                        source_label=self.source_label,
                    )
                )
            except Exception as exc:
                logger.debug("NAVER card 파싱 오류: %s", exc)

        return jobs