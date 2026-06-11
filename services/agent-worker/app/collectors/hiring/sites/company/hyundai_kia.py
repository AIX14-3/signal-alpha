"""
hyundai_kia.py
현대자동차 / 기아 공식 채용 사이트 크롤러 (Selenium ATS)

    현대자동차 : https://talent.hyundai.com/apply/applyList.hc
    기아        : https://career.kia.com/job/jobs.kc
"""

from __future__ import annotations

import logging
import re
import time

from ..base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

# ── URL ───────────────────────────────────────────────────────────────────────
_HYUNDAI_BASE = "https://talent.hyundai.com"
_HYUNDAI_LIST = f"{_HYUNDAI_BASE}/apply/applyList.hc"

_KIA_BASE = "https://career.kia.com"
_KIA_LIST = f"{_KIA_BASE}/job/jobs.kc"


# ── 현대자동차 ────────────────────────────────────────────────────────────────
class HyundaiCrawler(BaseSiteCrawler):
    source_label = "HYUNDAI_CAREERS"

    def crawl(self, company_name: str) -> list[dict]:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        self._safe_get(_HYUNDAI_LIST, wait_sec=3)

        try:
            self._wait_for(
                By.CSS_SELECTOR,
                "table.board-list, .apply-list tbody tr, .recruit-list li, [class*='apply-item'], table tbody tr",
                timeout=10,
            )
        except TimeoutException:
            logger.info("ℹ️  현대자동차: 공고 목록 로딩 실패")
            return []

        time.sleep(1.5)  # 비동기 데이터 렌더링 안정성을 위해 소폭 확장
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        return self._parse_hyundai(company_name, soup)

    def _extract_hyundai_url(self, link_el) -> str | None:
        """href나 onclick 속성에서 현대자동차 상세 페이지 이동 인자값을 추출하여 URL 완성."""
        if not link_el:
            return None

        href = link_el.get("href", "")
        onclick = link_el.get("onclick", "")
        
        # 1. 운 좋게 일반적인 텍스트 링크 구조로 풀 주소가 잡혀있을 경우
        if "applyView.hc" in href:
            return self.normalize_url(href, _HYUNDAI_BASE)

        # 2. 자바스크립트 함수 호출 형태일 경우 (cURL 단서 기반 파싱 처리)
        # 예: goView('2026', 'N4', '29') 또는 fn_view(2026, 'N4', 29) 등에서 따옴표 안의 값이나 숫자 추출
        combined_str = f"{href} {onclick}"
        
        # 괄호 안의 인자값들만 추출하기 위한 정규식 (따옴표 제거 포함)
        tokens = re.findall(r"['\"]?([a-zA-Z0-9_\-]+)['\"]?", combined_str)
        
        # 인자 중 연도 패턴(예: 2026)과 공고 유형 코드가 연속으로 감지되는 구간 파싱
        for i in range(len(tokens) - 2):
            # 첫 번째 토큰이 4자리 숫자(연도)이고 뒤이어 공고 분류 코드가 온다면 타겟으로 매칭
            if tokens[i].isdigit() and len(tokens[i]) == 4:
                recu_yy = tokens[i]
                recu_type = tokens[i+1]
                recu_cls = tokens[i+2]
                
                return f"{_HYUNDAI_BASE}/apply/applyView.hc?recuYy={recu_yy}&recuType={recu_type}&recuCls={recu_cls}"
        
        # 기본 폴백 처리
        return self.normalize_url(href, _HYUNDAI_BASE) if href and "javascript" not in href else _HYUNDAI_LIST

    def _parse_hyundai(self, company_name: str, soup) -> list[dict]:
        """현대자동차 ATS 목록 파싱 (table / dl / li 다중 시도)."""
        jobs: list[dict] = []

        # 시도 1: 테이블 행 기반 (<tr> with <a>)
        rows = soup.select("table tbody tr, .board-list tr")
        if rows:
            for row in rows:
                try:
                    link_el = row.find("a")
                    if not link_el:
                        continue
                    title = link_el.get_text(strip=True)
                    if not title:
                        continue
                        
                    # [수정] 자바스크립트 인자 추적 로직 적용
                    url = self._extract_hyundai_url(link_el) or _HYUNDAI_LIST
                    
                    cells = row.find_all("td")
                    deadline = cells[-1].get_text(strip=True) if len(cells) >= 3 else None
                    jobs.append(self._make_record(company_name, title, url, closing_date=deadline))
                except Exception as exc:
                    logger.debug("현대자동차 table 파싱 오류: %s", exc)

        # 시도 2: dl/dt/dd 패턴
        if not jobs:
            for dl in soup.select("dl.apply-item, dl.recruit-item, .apply-list dl"):
                try:
                    link_el = dl.find("a")
                    title_el = dl.find(["dt", "strong", "h4"])
                    if not (link_el or title_el):
                        continue
                    title = (title_el or link_el).get_text(strip=True)
                    
                    # [수정] 자바스크립트 인자 추적 로직 적용
                    url = self._extract_hyundai_url(link_el) or _HYUNDAI_LIST
                    
                    date_el = dl.find("dd", class_=lambda c: c and "date" in c.lower())
                    deadline = date_el.get_text(strip=True) if date_el else None
                    jobs.append(self._make_record(company_name, title, url, closing_date=deadline))
                except Exception as exc:
                    logger.debug("현대자동차 dl 파싱 오류: %s", exc)

        # 시도 3: 범용 li + a 패턴
        if not jobs:
            for li in soup.select("ul li, ol li"):
                try:
                    link_el = li.find("a")
                    if not link_el:
                        continue
                    title = link_el.get_text(strip=True)
                    if not title or len(title) < 4:
                        continue
                        
                    # [수정] 자바스크립트 인자 추적 로직 적용
                    url = self._extract_hyundai_url(link_el) or _HYUNDAI_LIST
                    jobs.append(self._make_record(company_name, title, url))
                except Exception:
                    pass

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