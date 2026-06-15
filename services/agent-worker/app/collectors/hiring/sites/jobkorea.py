"""
jobkorea.py
잡코리아 채용공고 크롤러
URL: https://www.jobkorea.co.kr/Search/?stext={company}

2026-06 기준 잡코리아 검색결과는 React/Tailwind 로 개편되어 의미 기반 클래스
(.list-post, .post-list-info 등)가 사라졌다. 안정적인 식별자는 공고 상세 링크
패턴(`/Recruit/GI_Read/<id>`)이므로, 이를 기준으로 카드를 잡고 제목/회사를 읽는다.
"""

from __future__ import annotations

import logging
import re
import time

from .base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

_BASE = "https://www.jobkorea.co.kr"
_SEARCH = f"{_BASE}/Search/"

# 공고 상세 링크: /Recruit/GI_Read/<숫자ID>  — 검색결과 레이아웃이 바뀌어도 유지되는 식별자
_JOB_ID = re.compile(r"/Recruit/GI_Read/(\d+)")
# Tailwind 유틸 클래스 중 디자인 토큰(text-typo-*)·의미 토큰은 비교적 안정적이다.
_LINK_SELECTOR = "a[href*='/Recruit/GI_Read/']"


class JobkoreaCrawler(BaseSiteCrawler):
    source_label = "JOBKOREA"

    def crawl(self, company_name: str) -> list[dict]:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By

        self._safe_get(f"{_SEARCH}?stext={company_name}")

        # 결과 로드 신호: 공고 상세 링크가 하나라도 나타나면 OK
        try:
            self._wait_for(By.CSS_SELECTOR, _LINK_SELECTOR, timeout=6)
        except TimeoutException:
            logger.info("ℹ️  잡코리아 [%s]: 공고 없음", company_name)
            return []

        time.sleep(1)
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        jobs: list[dict] = []
        seen: set[str] = set()

        # 공고 카드 1개 = div.shadow-list (상단 sticky 헤더는 GI_Read 링크가 없어 자동 제외)
        for card in soup.select("div.shadow-list"):
            try:
                link_el = card.find("a", href=_JOB_ID)
                if not link_el:
                    continue
                m = _JOB_ID.search(link_el.get("href", ""))
                if not m:
                    continue
                job_id = m.group(1)
                if job_id in seen:
                    continue

                # 제목: 제목 타이포 토큰(text-typo-b1-18). 광고 리본(text-typo-c1-13,
                # 예: "믿고보는 대기업")이 같은 font-semibold 라서, 크기 토큰으로 구분한다.
                title_el = card.find("span", class_=lambda c: c and "text-typo-b1-18" in c)
                title = title_el.get_text(strip=True) if title_el else link_el.get_text(strip=True)
                if not title:
                    continue
                seen.add(job_id)

                # 회사명: 본문 타이포 토큰(text-typo-b2-16). 없으면 검색어로 대체.
                corp_el = card.find("span", class_=lambda c: c and "text-typo-b2-16" in c)
                corp = corp_el.get_text(strip=True) if corp_el else company_name

                # 마감일: "마감" 이 포함된 caption 텍스트 (예: "06/15(월) 마감")
                deadline = None
                for sp in card.find_all("span", class_=lambda c: c and "text-typo-c1-13" in c):
                    txt = sp.get_text(strip=True)
                    if "마감" in txt:
                        deadline = txt
                        break

                # 쿼리스트링을 제거한 정규 상세 URL (external_id 안정화)
                job_url = f"{_BASE}/Recruit/GI_Read/{job_id}"

                jobs.append(self._make_record(
                    corp, title, job_url,
                    closing_date=deadline,
                ))
            except Exception as exc:
                logger.debug("잡코리아 파싱 오류: %s", exc)

        logger.info("✓ 잡코리아 [%s]: %d건 파싱", company_name, len(jobs))
        return jobs
