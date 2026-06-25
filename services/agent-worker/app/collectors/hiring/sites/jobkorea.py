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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")  # 게시일 stamp 기준(base_site/base_collector와 일치, #120)

_BASE = "https://www.jobkorea.co.kr"
_SEARCH = f"{_BASE}/Search/"

# 게시일('등록')에 묶인 날짜만 파싱 — 같은 카드의 마감일('~07/08(수)')과 혼동 방지.
#   상대형: "21시간 전 등록" / "3일 전 등록" / "방금 등록"
#   절대형: "06/16(화)등록"
_REG_REL_DAYS = re.compile(r"(\d+)\s*일\s*전\s*등록")
_REG_REL_HM = re.compile(r"(?:\d+\s*(?:시간|분)|방금)\s*전?\s*등록")
_REG_ABS = re.compile(r"(\d{1,2})/(\d{1,2})\s*(?:\([월화수목금토일]\))?\s*등록")


def parse_posting_date(text: str | None, anchor: datetime | None = None) -> str | None:
    """잡코리아 목록의 '등록' 텍스트 → 게시일 ``YYYY-MM-DD``. 못 찾으면 None.

    None 이면 호출부(_make_record)가 수집 시각으로 폴백한다. '등록'에 묶인 날짜만 보므로
    같은 카드의 마감일('~07/08(수)')을 게시일로 오인하지 않는다. 상대형('N일 전 등록' /
    'N시간·분 전 등록' / '방금 등록')은 anchor(기본 KST now) 기준, 절대형('06/16(화)등록')은
    연도 추론(등록월 > 수집월이면 작년 — 연말 등록을 연초 수집).
    """
    if not text:
        return None
    anchor = anchor or datetime.now(_KST)
    if m := _REG_REL_DAYS.search(text):
        return (anchor - timedelta(days=int(m.group(1)))).date().isoformat()
    if _REG_REL_HM.search(text):
        return anchor.date().isoformat()   # 24시간 내 → 오늘(KST)
    if m := _REG_ABS.search(text):
        month, day = int(m.group(1)), int(m.group(2))
        year = anchor.year - 1 if month > anchor.month else anchor.year
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None  # 02/30 같은 비정상 날짜
    return None

# 공고 상세 링크: /Recruit/GI_Read/<숫자ID>  — 검색결과 레이아웃이 바뀌어도 유지되는 식별자
_JOB_ID = re.compile(r"/Recruit/GI_Read/(\d+)")
# Tailwind 유틸 클래스 중 디자인 토큰(text-typo-*)·의미 토큰은 비교적 안정적이다.
_LINK_SELECTOR = "a[href*='/Recruit/GI_Read/']"

# 회원번호(기업 고유 ID) 기반 직접 수집(#313, 스파이크 #266). 그 법인 진행공고만
# 서버렌더로 제공 → 키워드검색의 협력사·대리점 노이즈 원천 차단.
_MEMBER_LIST = "/Recruit/Co_Read/Recruit/C/{cid}"   # 기업 '채용공고' 탭(공고 리스트)
_COMPANY_PAGE = "/Recruit/Co_Read/C/{cid}"           # 기업정보 페이지(Referer 용)
# 페이지 title("… 진행 중인 공고 총 N건")의 N = ground-truth 공고 수 → 0건 vs DOM깨짐 구별.
_COUNT_RE = re.compile(r"총\s*([\d,]+)\s*건")


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

                # 게시일: 카드 텍스트의 '등록'에 묶인 날짜(상대/절대). 없으면 수집시각 폴백.
                posting_date = parse_posting_date(card.get_text(" ", strip=True))

                jobs.append(self._make_record(
                    corp, title, job_url,
                    closing_date=deadline,
                    posting_date=posting_date,
                ))
            except Exception as exc:
                logger.debug("잡코리아 파싱 오류: %s", exc)

        logger.info("✓ 잡코리아 [%s]: %d건 파싱", company_name, len(jobs))
        return jobs

    # ── 회원번호(기업 고유 ID) 기반 직접 수집 (#313) ────────────────────────────
    def crawl_by_member_id(self, member_id: str, company_name: str) -> list[dict] | None:
        """잡코리아 회원번호로 그 법인의 진행공고만 직접 수집(협력사 노이즈 0).

        서버렌더라 Selenium 불요 — 공용 http.py(retry/UA로테이션/차단센서)로 GET.
        반환 list = 파싱 성공(0건 포함). **None = DOM 구조 변경 의심 → 호출부가 키워드 폴백.**
        """
        from . import http

        url = f"{_BASE}{_MEMBER_LIST.format(cid=member_id)}"
        ref = f"{_BASE}{_COMPANY_PAGE.format(cid=member_id)}"
        try:
            resp = http.get(url, headers={"Referer": ref})
        except Exception as exc:
            logger.warning(
                "잡코리아 [%s] 회원번호 경로 요청 실패: %s — 키워드 폴백", company_name, exc
            )
            return None
        return self._parse_member_list(resp.text, company_name)

    def _parse_member_list(self, html: str, company_name: str) -> list[dict] | None:
        """회원번호 기업 공고 리스트 HTML → 표준 레코드. 순수 함수(네트워크 없음, 테스트 격리).

        가드(0건 vs DOM깨짐): title의 '총 N건'(ground-truth)이 N>0인데 카드가 0개면
        구조 변경으로 보고 None 반환(호출부 키워드 폴백) — 무음 회귀 방지.
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else ""
        m = _COUNT_RE.search(title)
        declared = int(m.group(1).replace(",", "")) if m else None

        jobs: list[dict] = []
        seen: set[str] = set()
        # 카드 1개 = div.list-item (제목 dt.tit / 마감 dd.func span.day / 링크 GI_Read).
        for card in soup.select("div.list-item"):
            link_el = card.find("a", href=_JOB_ID)
            if not link_el:
                continue
            mm = _JOB_ID.search(link_el.get("href", ""))
            if not mm:
                continue
            job_id = mm.group(1)
            if job_id in seen:
                continue

            tit_el = card.select_one("dt.tit")
            title_text = (tit_el.get_text(strip=True) if tit_el
                          else link_el.get_text(strip=True))
            if not title_text:
                continue
            seen.add(job_id)

            day_el = card.select_one("dd.func span.day") or card.select_one("span.day")
            deadline = day_el.get_text(strip=True) if day_el else None

            posting_date = parse_posting_date(card.get_text(" ", strip=True))

            job_url = f"{_BASE}/Recruit/GI_Read/{job_id}"
            jobs.append(self._make_record(
                company_name, title_text, job_url, closing_date=deadline,
                posting_date=posting_date,
            ))

        if not jobs and declared:
            logger.warning(
                "잡코리아 [%s] 회원번호 경로: title은 %d건인데 카드 0개 — DOM 변경 의심, 키워드 폴백",
                company_name, declared,
            )
            return None
        logger.info(
            "✓ 잡코리아 [%s] 회원번호 직접수집: %d건 (title 신고 %s건)",
            company_name, len(jobs), declared if declared is not None else "?",
        )
        return jobs
