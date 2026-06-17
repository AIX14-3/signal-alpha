"""NAVER 자체 DOM 채용관 파서 단위 테스트 (#175 / #234).

- rcrt/list.do 의 SSR 카드(li.card_item) 파싱
- 상세 링크는 a.card_link[onclick="show('annoId')"] → /rcrt/view.do?annoId=N&lang=ko
- 직무명 .card_title, 마감일 모집 기간 종료일(YYYY.MM.DD → YYYY-MM-DD), 상시 등은 None
- 마감 카드(li.card_item.disabled) 제외, share()/copyUrl() annoId는 미수집
- 과거 _parse_html_elements 의 job["url"] KeyError 회귀 방지(_make_record는 source_url 키)
- CollectorResult 로 raw_payload·source_label 보존(4b reparse 호환)
"""

from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app.collectors.hiring.base_collector import CollectorResult
from app.collectors.hiring.sites.company.naver import NaverCrawler

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hiring" / "NAVER.html"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _load_fixture() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


class NaverParserTest(unittest.TestCase):
    def setUp(self):
        self.c = NaverCrawler(driver=None)
        raw = _load_fixture()
        self.results = self.c._parse_naver(_soup(raw), "네이버", raw)

    def test_active_cards_only(self):
        """활성 카드 2건만(마감 disabled 카드 제외), 전부 CollectorResult."""
        self.assertEqual(len(self.results), 2)
        self.assertTrue(all(isinstance(r, CollectorResult) for r in self.results))

    def test_titles(self):
        titles = [r.data["job_title"] for r in self.results]
        self.assertIn("[NAVER] 2026 NAVER 하계 인턴십 : 비즈니스 트랙 (체험형)", titles)
        self.assertIn("[NAVER] 경영관리 담당(FP&A) (경력)", titles)  # &amp; 디코딩
        self.assertNotIn("[NAVER] 마감된 공고 예시", titles)  # disabled 제외

    def test_detail_url_from_onclick_annoid(self):
        first = next(r for r in self.results if "하계 인턴십" in r.data["job_title"])
        self.assertEqual(
            first.data["source_url"],
            "https://recruit.navercorp.com/rcrt/view.do?annoId=30004975&lang=ko",
        )

    def test_closing_date_normalized(self):
        first = next(r for r in self.results if "하계 인턴십" in r.data["job_title"])
        self.assertEqual(first.data["closing_date"], "2026-06-22")  # 종료일, dots→dashes

    def test_no_date_posting_has_none_closing(self):
        sangsi = next(r for r in self.results if "경영관리" in r.data["job_title"])
        self.assertIsNone(sangsi.data["closing_date"])  # '상시' → 날짜 없음

    def test_share_links_not_collected(self):
        """share()/copyUrl() 의 annoId 링크는 공고로 수집되지 않음(show()만)."""
        urls = [r.data["source_url"] for r in self.results]
        self.assertEqual(len(urls), len(set(urls)))  # 카드당 1건, 중복 없음
        self.assertEqual(len(urls), 2)

    def test_raw_payload_and_label_preserved(self):
        raw = _load_fixture()
        for r in self.results:
            self.assertEqual(r.raw_payload, raw)
            self.assertEqual(r.source_label, "NAVER_CAREERS")


class NaverEmptyPageTest(unittest.TestCase):
    def test_empty_card_list_returns_empty(self):
        html = '<ul class="card_list"><p class="result_text">진행 중인 공고가 없습니다.</p></ul>'
        c = NaverCrawler(driver=None)
        self.assertEqual(c._parse_naver(_soup(html), "네이버", html), [])

    def test_no_cards_returns_empty(self):
        c = NaverCrawler(driver=None)
        self.assertEqual(c._parse_naver(_soup("<div/>"), "네이버", "<div/>"), [])


if __name__ == "__main__":
    unittest.main()
