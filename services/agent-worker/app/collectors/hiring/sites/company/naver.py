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

    def crawl(self, company_name: str) -> list[dict]:
        """NAVER 채용 공고 수집 (HTML SSR 파싱 기법 적용)."""
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": _BASE,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        }
        
        params = {
            "pageNo": 1,
        }

        jobs: list[dict] = []
        try:
            # 1차 시도: requests
            resp = requests.get(_LIST_URL, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, "html.parser")
            jobs = self._parse_html_elements(soup, company_name)

            if not jobs and self.driver:
                logger.info("NAVER 1차 파싱 결과 없음 -> Selenium 폴백 전환")
                jobs = self._selenium_fallback(company_name)

        except Exception as exc:
            logger.warning("NAVER 채용 1차 시도 오류: %s", exc)
            if self.driver:
                jobs = self._selenium_fallback(company_name)

        return jobs

    def _selenium_fallback(self, company_name: str) -> list[dict]:
        """Selenium 폴백: 브라우저를 띄워 진짜 목록 페이지로 이동 후 수집."""
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        self._safe_get(_LIST_URL)
        
        try:
            # [핵심 변경] 깨지기 쉬운 클래스명 대신, 상세페이지 링크 패턴(view.do 또는 annoId)을 가진 <a> 태그가 나타날 때까지 대기
            self._wait_for(By.CSS_SELECTOR, 'a[href*="view.do"], a[href*="annoId"]', timeout=12)
        except TimeoutException:
            logger.warning("⚠️ NAVER Selenium 폴백 타임아웃: 공고 상세 링크 요소를 찾을 수 없음")
            
            # [디버깅 꿀팁] 도대체 무엇이 렌더링되었는지 확인하기 위해 껍데기 소스를 로깅해봅니다.
            snippet = self.driver.page_source[:1000]
            logger.debug("현재 페이지 앞부분 소스 힌트: %s", snippet)
            return []

        # 브라우저가 최종 완성한 완성본 HTML 소스를 받아와 파싱
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        return self._parse_html_elements(soup, company_name)

    def _parse_html_elements(self, soup: BeautifulSoup, company_name: str) -> list[dict]:
        """HTML 구조에서 공고명, 링크, 마감일을 추출하는 강력하고 유연한 헬퍼 메서드."""
        import re
        jobs: list[dict] = []
        
        # 상세 페이지 주소 규칙을 가진 모든 <a> 태그를 직접 타겟팅 (가장 확실한 뼈대)
        link_elements = soup.select('a[href*="view.do"], a[href*="annoId"]')
        
        for link_el in link_elements:
            try:
                raw_href = link_el.get("href", "")
                
                # 확실하게 고유 번호(annoId)가 포함된 링크만 필터링
                if "annoId=" not in raw_href:
                    continue
                    
                url = self.normalize_url(raw_href, _BASE)
                
                # 1. <a> 태그 내부에 제목 역할을 하는 하위 태그가 있는지 확인
                title_el = link_el.select_one("strong, h4, h5, .title, span")
                if title_el:
                    title = title_el.get_text(strip=True)
                else:
                    # 2. 만약 <a> 태그가 카드 컴포넌트 전체를 감싸고 있다면 텍스트 전체를 확보
                    title = link_el.get_text(" ", strip=True)
                
                # 텍스트 가공 및 불필요한 공백 제거
                title = re.sub(r'\s+', ' ', title).strip()
                if not title or len(title) < 4:
                    continue
                
                # 마감일 추출 (<a> 내부 혹은 부모 컴포넌트 주변에서 날짜 클래스 탐색)
                parent = link_el.parent
                deadline_el = (link_el.select_one(".date, .end-date, .card_sub") or 
                               parent.select_one(".date, .end-date, .card_sub"))
                
                deadline = deadline_el.get_text(strip=True) if deadline_el else None
                
                # 날짜 엘리먼트 클래스가 안 맞을 경우를 대비해 텍스트에서 'YYYY.MM.DD' 정규식 추출 폴백
                if not deadline:
                    date_match = re.search(r"\d{4}\.\d{2}\.\d{2}", link_el.get_text())
                    if date_match:
                        deadline = date_match.group()

                jobs.append(self._make_record(
                    company_name, title, url, closing_date=deadline
                ))
            except Exception as e:
                logger.debug("요소 개별 파싱 오류: %s", e)
                
        # 중복 제거 (동일한 annoId 주소가 여러 개 수집되었을 경우를 대비)
        seen_urls = set()
        unique_jobs = []
        for job in jobs:
            if job["url"] not in seen_urls:
                seen_urls.add(job["url"])
                unique_jobs.append(job)
                
        return unique_jobs