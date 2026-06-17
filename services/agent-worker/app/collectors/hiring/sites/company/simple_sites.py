"""
simple_sites.py
정적/CMS 기반 기업 공식 채용 사이트 파서

대상:
    한미반도체        https://www.hanmisemi.com/?module=Html&action=SiteComp&sSubNo=17
    스튜디오드래곤    https://www.studiodragon.net/ko/etc/talent/
    삼성바이오로직스  https://samsungbiologics.com/kr/careers/apply/how-to-apply

이 사이트들은 JavaScript 렌더링이 최소화된 정적/CMS 페이지여서
requests + BeautifulSoup 으로 처리 시도하고, 실패 시 Selenium 으로 폴백.
"""

from __future__ import annotations

import logging

from ..base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

# 기업별 설정: (URL, source_label, 전용 파서 메서드명)
_SIMPLE_SITES: dict[str, tuple[str, str, str]] = {
    "한미반도체":       (
        "https://www.hanmisemi.com/?module=Html&action=SiteComp&sSubNo=17",
        "HANMI_SEMI_CAREERS",
        "_parse_hanmi",
    ),
    "스튜디오드래곤": (
        "https://www.studiodragon.net/ko/etc/talent/",
        "STUDIO_DRAGON_CAREERS",
        "_parse_studio_dragon",
    ),
    "삼성바이오로직스": (
        "https://samsungbiologics.com/kr/careers/apply/how-to-apply",
        "SAMSUNG_BIO_CAREERS",
        "_parse_samsung_bio",
    ),
}


class SimpleSiteCrawler(BaseSiteCrawler):
    """정적/CMS 기업 사이트 통합 크롤러."""

    source_label = "COMPANY_STATIC"

    def crawl(self, company_name: str) -> list:
        if company_name not in _SIMPLE_SITES:
            return []
        url, label, parser_name = _SIMPLE_SITES[company_name]
        self.source_label = label
        # Phase 4b: 원본 HTML(raw_html)을 함께 받아 파서에 전달 → 격리 시 raw_payload 보존.
        soup, raw_html = self._fetch_soup(url)
        if soup is None:
            return []
        parser = getattr(self, parser_name)
        return parser(company_name, url, soup, raw_html)

    def _fetch_soup(self, url: str):
        """requests → (BeautifulSoup, 원본 HTML), 실패 시 Selenium 폴백. 실패 시 (None, None)."""
        from bs4 import BeautifulSoup
        try:
            from ..http import get as http_get
            # User-Agent는 http_get이 풀에서 로테이션 주입한다.
            resp = http_get(url)
            return BeautifulSoup(resp.text, "html.parser"), resp.text
        except Exception as exc:
            logger.debug("requests 실패(%s) → Selenium 폴백: %s", url, exc)

        # Selenium 폴백
        if self.driver is None:
            logger.warning("⚠️  Selenium driver 없음, %s 스킵", url)
            return None, None
        try:
            self._safe_get(url, wait_sec=2)
            page = self.driver.page_source
            return BeautifulSoup(page, "html.parser"), page
        except Exception as exc:
            logger.error("Selenium 폴백 실패(%s): %s", url, exc)
            return None, None

    # ── 한미반도체 ───────────────────────────────────────────────────────────────
    def _parse_hanmi(self, company: str, base_url: str, soup, raw_html=None) -> list[dict]:
        """한미반도체 채용 페이지 파싱 (HTML CMS). (legacy dict — raw_html 미사용)"""
        jobs: list[dict] = []
        # 공고 목록: 테이블 또는 ul.board-list
        for row in (
            soup.select("table.board-list tr:not(:first-child)")
            or soup.select("ul.board-list li")
            or soup.select(".recruit-list .item")
        ):
            try:
                link_el = row.find("a")
                if not link_el:
                    continue
                title = link_el.get_text(strip=True)
                url = self.normalize_url(link_el.get("href", ""), base_url)

                date_el = row.find("td", class_=lambda c: c and "date" in c.lower()) \
                    or row.find("span", class_=lambda c: c and "date" in c.lower())
                deadline = date_el.get_text(strip=True) if date_el else None

                jobs.append(self._make_record(company, title, url, closing_date=deadline))
            except Exception as exc:
                logger.debug("한미반도체 파싱 오류: %s", exc)
        return jobs

    # ── 스튜디오드래곤 ───────────────────────────────────────────────────────────
    def _parse_studio_dragon(self, company: str, base_url: str, soup, raw_html=None) -> list[dict]:
        """스튜디오드래곤 채용 페이지 파싱. (legacy dict — raw_html 미사용)"""
        jobs: list[dict] = []
        for item in (
            soup.select(".talent-list li")
            or soup.select(".recruit-list li")
            or soup.select("ul.list li")
            or soup.select(".board-list tr")
        ):
            try:
                link_el = item.find("a")
                title_el = item.find(["strong", "h3", "h4", "p"])
                if not (link_el or title_el):
                    continue
                title = (title_el or link_el).get_text(strip=True)
                url = self.normalize_url(
                    (link_el.get("href", "") if link_el else ""), base_url
                )
                jobs.append(self._make_record(company, title, url))
            except Exception as exc:
                logger.debug("스튜디오드래곤 파싱 오류: %s", exc)
        return jobs

    # ── 삼성바이오로직스 ─────────────────────────────────────────────────────────
    def _parse_samsung_bio(self, company: str, base_url: str, soup, raw_html=None) -> list:
        """삼성바이오로직스 — '채용 안내' 1건만 반환 (#175 종결).

        삼성바이오 채용 사이트는 **상시 직무 공고 목록을 노출하지 않는다**(확인: how-to-apply
        페이지의 a[href*='career'] 링크는 전부 네비 메뉴 — CAREERS/복지제도/FAQs/인재상/
        인재Pool 등). 채용 방식이 '시즌 공개채용 + 직무분야 안내 + 국내경력 인재Pool 등록'
        이라 긁을 공고가 없다. 메뉴를 긁으면 공고로 오파싱(오염)되므로 career 링크 수집을
        제거하고 안내 1건만 기록한다(공식 채용관 생존 신호).

        - 안내 1건은 분석의 MIN_TODAY_JOB_COUNT(3) 미만이라 hiring_signals 에 영향 없음.
        - 실질 삼성바이오 공고 신호는 포털(잡코리아) 경로가 커버한다.
        - raw_html 은 CollectorResult 로 보존(미래에 사이트가 공고 목록을 노출하면
          격리/reparse 기반 복구 가능).
        """
        from ...base_collector import CollectorResult

        return [CollectorResult(
            data=self._make_record(
                company,
                "삼성바이오로직스 채용 안내",
                base_url,
                job_description=(
                    "공식 채용 안내 페이지. 상시 공고 목록 미노출"
                    "(시즌 공개채용/직무분야 안내/인재Pool). 실 공고는 포털 경로 참조."
                ),
            ),
            raw_payload=raw_html,
            source_label=self.source_label,
        )]
