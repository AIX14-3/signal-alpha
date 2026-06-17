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
    # 안내 메뉴(공고 아님) 식별용 휴리스틱 블랙리스트(제목 casefold 부분일치).
    # ⚠️ 임시 1차 — 현재 selector(a[href*='career'] 등)가 광범위해 메뉴를 오파싱한다.
    #    정밀 컨테이너 selector 는 raw_payload 실HTML 확보 후 후속(#175 종결).
    #    href 의 'career' 경로는 검사하지 않는다(실공고 URL 도 /careers/ 라 과소수집 방지).
    _MENU_BLOCKLIST = {
        "faq", "복지", "다양성", "사내복지", "자기개발",
        "채용절차", "채용안내", "채용지원", "careers", "welfare", "benefit",
    }

    def _parse_samsung_bio(self, company: str, base_url: str, soup, raw_html=None) -> list:
        """삼성바이오로직스 채용 페이지 파싱 (Phase 4b — CollectorResult 전환).

        각 레코드를 CollectorResult 로 감싸 원본 HTML(raw_html)을 보존한다 → 게이트 거부/
        오류 시 hiring_quarantine 에 raw_payload 로 격리되어, selector 수정 후
        replay-reparse(유실 제로 backfill)가 가능해진다.
        """
        from ...base_collector import CollectorResult

        results: list = []
        for link_el in soup.select("a[href*='career'], a[href*='recruit'], a[href*='job']"):
            title = link_el.get_text(strip=True)
            if not title or len(title) < 4:
                continue
            # 휴리스틱: 안내 메뉴 제목은 공고가 아니므로 제외(1차 — 정밀 selector 는 후속).
            if any(tok in title.casefold() for tok in self._MENU_BLOCKLIST):
                continue
            url = self.normalize_url(link_el.get("href", ""), base_url)
            results.append(CollectorResult(
                data=self._make_record(company, title, url),
                raw_payload=raw_html,
                source_label=self.source_label,
            ))

        if not results:
            # 공고 링크 없음 → 채용 안내 페이지 1건 등록 (공고 집계용)
            results.append(CollectorResult(
                data=self._make_record(
                    company,
                    "삼성바이오로직스 채용 안내",
                    base_url,
                    job_description="공식 채용 안내 페이지. 개별 공고는 사이트 직접 확인 요망.",
                ),
                raw_payload=raw_html,
                source_label=self.source_label,
            ))
        return results
