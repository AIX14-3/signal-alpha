"""잡코리아 회원번호 직접수집 파서 단위 테스트 (#313).

- 회원번호 공고 리스트 HTML(div.list-item: dt.tit 제목 / dd.func span.day 마감 /
  a[href*=GI_Read] 링크)을 표준 레코드로 파싱.
- 0건 vs DOM깨짐 가드: title의 '총 N건'이 N>0인데 카드 0개면 None(키워드 폴백).
- title이 '총 0건'이고 카드 0개면 빈 리스트(진짜 공고 없음).
- 네트워크 없음 — 순수 함수 `_parse_member_list` 직접 호출.
"""

from __future__ import annotations

import unittest

from app.collectors.hiring.sites.jobkorea import JobkoreaCrawler


def _card(job_id: str, title: str, dday: str) -> str:
    return f"""
      <div class="list-item">
        <dl class="booth">
          <a class="AgiLink" href="/Recruit/GI_Read/{job_id}">
            <dt class="tit">{title}</dt>
            <dd class="trm"><span class="cell add">경력</span><span class="cell">경기</span></dd>
            <dd class="func clear"><span class="day tahoma">{dday}</span></dd>
          </a>
        </dl>
      </div>
    """


def _page(title_count: int | None, cards: str) -> str:
    title = (
        f"<title>네이버 채용 - 2026년 진행 중인 공고 총 {title_count}건 | 잡코리아</title>"
        if title_count is not None
        else "<title>네이버 채용 | 잡코리아</title>"
    )
    return f"<html><head>{title}</head><body><div class='list'>{cards}</div></body></html>"


class JobkoreaMemberParseTest(unittest.TestCase):
    def setUp(self):
        self.c = JobkoreaCrawler(driver=None)

    def test_parses_cards_to_records(self):
        html = _page(2, _card("49402765", "경영관리 담당(FP&A)", "D-11")
                     + _card("49390248", "법인세/국제조세 담당", "D-3"))
        out = self.c._parse_member_list(html, "NAVER")
        self.assertEqual(len(out), 2)
        self.assertEqual([r["job_title"] for r in out],
                         ["경영관리 담당(FP&A)", "법인세/국제조세 담당"])
        self.assertEqual(out[0]["source_type"], "JOBKOREA")
        self.assertEqual(out[0]["source_url"],
                         "https://www.jobkorea.co.kr/Recruit/GI_Read/49402765")
        self.assertEqual(out[0]["closing_date"], "D-11")
        self.assertEqual(out[0]["company_name"], "NAVER")

    def test_dedupes_repeated_job_id(self):
        html = _page(1, _card("49402765", "중복 공고", "D-1")
                     + _card("49402765", "중복 공고", "D-1"))
        out = self.c._parse_member_list(html, "NAVER")
        self.assertEqual(len(out), 1)

    def test_dom_broken_returns_none_for_fallback(self):
        """title은 5건인데 카드 0개 → DOM 변경 의심 → None(키워드 폴백)."""
        out = self.c._parse_member_list(_page(5, ""), "NAVER")
        self.assertIsNone(out)

    def test_genuine_empty_returns_empty_list(self):
        """title이 0건이고 카드 0개 → 진짜 공고 없음 → 빈 리스트(폴백 아님)."""
        out = self.c._parse_member_list(_page(0, ""), "NAVER")
        self.assertEqual(out, [])

    def test_no_count_no_cards_returns_empty(self):
        """title에 '총 N건'이 없고 카드도 0개 → declared=None → 빈 리스트(폴백 트리거 안 함)."""
        out = self.c._parse_member_list(_page(None, ""), "NAVER")
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
