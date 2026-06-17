"""
hyundai_kia.py
현대자동차 / 기아 공식 채용 사이트 크롤러 (Selenium ATS)

    현대자동차 : https://talent.hyundai.com/apply/applyList.hc
    기아        : https://career.kia.com/job/jobs.kc
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ..base_site import BaseSiteCrawler

if TYPE_CHECKING:
    from bs4.element import Tag

logger = logging.getLogger(__name__)

# ── URL ───────────────────────────────────────────────────────────────────────
_HYUNDAI_BASE = "https://talent.hyundai.com"
_HYUNDAI_LIST = f"{_HYUNDAI_BASE}/apply/applyList.hc"

_KIA_BASE = "https://career.kia.com"
_KIA_LIST = f"{_KIA_BASE}/job/jobs.kc"


# ── 현대자동차 ────────────────────────────────────────────────────────────────
class HyundaiCrawler(BaseSiteCrawler):
    source_label = "HYUNDAI_CAREERS"

    def crawl(self, company_name: str) -> list:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        self._safe_get(_HYUNDAI_LIST, wait_sec=3)

        try:
            # 공고가 있으면 li, 비수기라 없으면 board-empty 둘 중 하나로 렌더 완료 판정
            self._wait_for(
                By.CSS_SELECTOR,
                "ul.apply__list > li, article.board-empty",
                timeout=10,
            )
        except TimeoutException:
            logger.info("ℹ️  현대자동차: 공고 목록 로딩 실패")
            return []

        time.sleep(1.5)  # 비동기 데이터 렌더링 안정성을 위해 소폭 확장
        raw_html = self.driver.page_source
        soup = BeautifulSoup(raw_html, "html.parser")
        return self._parse_hyundai(company_name, soup, raw_html)

    def _extract_hyundai_url(self, li_tag: Tag) -> str:
        """공고 li 의 data-* 속성으로 상세 페이지 GET URL 을 조립(그룹/일반 분기)."""
        ntc_group_no = (li_tag.get("data-ntcgroupno") or "").strip()
        is_group = "group" in (li_tag.get("class") or []) or (
            bool(ntc_group_no) and ntc_group_no != "0"
        )
        if is_group:
            return f"{_HYUNDAI_BASE}/apply/applyGroupView.hc?ntcGroupNo={ntc_group_no}"

        recu_yy = (li_tag.get("data-recuyy") or "").strip()
        recu_type = (li_tag.get("data-recutype") or "").strip()
        recu_cls = (li_tag.get("data-recucls") or "").strip()
        return (
            f"{_HYUNDAI_BASE}/apply/applyView.hc"
            f"?recuYy={recu_yy}&recuType={recu_type}&recuCls={recu_cls}"
        )

    def _parse_hyundai(self, company_name: str, soup, raw_html: str | None = None) -> list:
        """현대자동차 React 채용관(apply__list) 목록 파싱.

        공고 데이터는 ``ul.apply__list > li`` 의 data-* 속성에 모두 들어있다.
        각 건을 CollectorResult 로 감싸 raw_payload·source_label 을 보존한다(4b reparse 호환).
        공고가 없으면(board-empty) 빈 리스트를 반환한다.
        """
        from ...base_collector import CollectorResult

        jobs: list = []
        for li in soup.select("ul.apply__list > li"):
            try:
                title = (li.get("data-recunoticenm") or "").strip()
                if not title:
                    strong = li.select_one("div.top strong")
                    title = strong.get_text(strip=True) if strong else ""
                if not title:
                    continue

                url = self._extract_hyundai_url(li)

                # data-dispdate: "2026-06-15 09:00 ~ 2026-06-18 23:59" → 종료일 YYYY-MM-DD
                disp_date = (li.get("data-dispdate") or "").strip()
                if disp_date:
                    deadline = disp_date.split("~")[-1].strip()[:10]
                else:
                    deadline = (li.get("data-dispdday") or "").strip() or None

                jobs.append(
                    CollectorResult(
                        data=self._make_record(
                            company_name, title, url, closing_date=deadline
                        ),
                        raw_payload=raw_html,
                        source_label=self.source_label,
                    )
                )
            except Exception as exc:
                logger.debug("현대자동차 apply__list 파싱 오류: %s", exc)

        return jobs


# ── 기아 ──────────────────────────────────────────────────────────────────────
class KiaCrawler(BaseSiteCrawler):
    source_label = "KIA_CAREERS"

    def crawl(self, company_name: str) -> list[dict]:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        self._safe_get(_KIA_LIST, wait_sec=3)

        try:
            self._wait_for(
                By.CSS_SELECTOR,
                ".job-list li, .recruit-list li, table.board tbody tr, [class*='job-item']",
                timeout=10,
            )
        except TimeoutException:
            logger.info("ℹ️  기아: 공고 목록 로딩 실패")
            return []

        self._click_more_button(max_clicks=5)

        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        return self._parse_kia(company_name, soup)

    def _click_more_button(self, max_clicks: int = 5) -> None:
        """더보기/Load More 버튼 클릭."""
        from selenium.common.exceptions import NoSuchElementException, ElementNotInteractableException
        from selenium.webdriver.common.by import By

        clicked = 0
        for _ in range(max_clicks):
            try:
                btn = self.driver.find_element(
                    By.XPATH,
                    "//button[contains(text(), '더보기') or contains(text(), 'More') "
                    "or contains(@class, 'btn-more') or contains(@class, 'load-more')]",
                )
                btn.click()
                time.sleep(1.5)
                clicked += 1
            except (NoSuchElementException, ElementNotInteractableException):
                break
        if clicked:
            logger.debug("기아 더보기 %d회 클릭", clicked)

    def _parse_kia(self, company_name: str, soup) -> list[dict]:
        """기아 ATS 목록 파싱."""
        jobs: list[dict] = []

        items = (
            soup.select(".job-list li")
            or soup.select(".recruit-list li")
            or soup.select("[class*='job-item']")
        )
        if items:
            for item in items:
                try:
                    link_el = item.find("a")
                    title_el = item.find(["strong", "h3", "h4", "p"])
                    if not (link_el or title_el):
                        continue
                    title = (title_el or link_el).get_text(strip=True)
                    if not title or len(title) < 3:
                        continue
                    url = self.normalize_url(
                        (link_el.get("href", "") if link_el else ""), _KIA_BASE
                    )
                    date_el = item.find(
                        lambda t: t.name in ("span", "em") and "date" in (t.get("class") or [""])[0].lower()
                    )
                    deadline = date_el.get_text(strip=True) if date_el else None
                    jobs.append(self._make_record(company_name, title, url, closing_date=deadline))
                except Exception as exc:
                    logger.debug("기아 list 파싱 오류: %s", exc)

        if not jobs:
            for row in soup.select("table tbody tr"):
                try:
                    link_el = row.find("a")
                    if not link_el:
                        continue
                    title = link_el.get_text(strip=True)
                    if not title or len(title) < 3:
                        continue
                    url = self.normalize_url(link_el.get("href", ""), _KIA_BASE)
                    cells = row.find_all("td")
                    deadline = cells[-1].get_text(strip=True) if len(cells) >= 3 else None
                    jobs.append(self._make_record(company_name, title, url, closing_date=deadline))
                except Exception as exc:
                    logger.debug("기아 table 파싱 오류: %s", exc)

        return jobs