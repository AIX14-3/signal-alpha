"""SK하이닉스 자체 DOM 채용관 파서 단위 테스트 (#175 / #233).

- /hub/ko/apply/job 의 '지원 직무' 링크(.list-item > a[/hub/ko/job/introduce?id=N])를 공고로 파싱
- 직무명은 .title, 상세 URL 은 introduce?id 링크
- 네비 메뉴의 bare introduce(?id 없음) 링크는 제외
- CollectorResult 로 raw_payload·source_label 보존(4b reparse 호환)
- 공고 없음 시 빈 리스트 반환
"""

from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app.collectors.hiring.base_collector import CollectorResult
from app.collectors.hiring.sites.company.sk_hynix import SKHynixCrawler

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hiring" / "SK_HYNIX.html"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _load_fixture() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


class SKHynixParserTest(unittest.TestCase):
    def setUp(self):
        self.c = SKHynixCrawler(driver=None)
        raw = _load_fixture()
        self.results = self.c._parse_sk_hynix("SK하이닉스", _soup(raw), raw)

    def test_returns_collector_result_per_introduce_link(self):
        self.assertEqual(len(self.results), 3)  # 픽스처 introduce?id 링크 3건
        self.assertTrue(all(isinstance(r, CollectorResult) for r in self.results))

    def test_titles_from_title_div(self):
        titles = [r.data["job_title"] for r in self.results]
        self.assertEqual(titles, ["설계", "소자", "R&D 공정"])

    def test_nav_bare_introduce_excluded(self):
        """?id 없는 네비 메뉴 introduce 링크('채용공고')는 공고로 수집되지 않음."""
        titles = [r.data["job_title"] for r in self.results]
        self.assertNotIn("채용공고", titles)

    def test_detail_url_built(self):
        first = self.results[0]
        url = first.data["source_url"]
        self.assertEqual(url, "https://talent.skhynix.com/hub/ko/job/introduce?id=1070")

    def test_raw_payload_and_label_preserved(self):
        raw = _load_fixture()
        for r in self.results:
            self.assertEqual(r.raw_payload, raw)
            self.assertEqual(r.source_label, "SK_HYNIX_CAREERS")


class SKHynixEmptyPageTest(unittest.TestCase):
    def test_coming_soon_only_returns_empty_list(self):
        """채용중 공고 없이 coming-soon 골격(introduce 링크 미렌더)만 있으면 빈 리스트."""
        html = """
            <div class="recruit-lnb">
              <div class="accordion-list recruit-lnb-item coming-soon">
                <div class="text">7월 모집 예정 공고</div>
                <div class="accordion-body lnb-list-body"></div>
              </div>
            </div>
            <nav><a href="/hub/ko/job/introduce">채용공고</a></nav>
        """
        c = SKHynixCrawler(driver=None)
        out = c._parse_sk_hynix("SK하이닉스", _soup(html), html)
        self.assertEqual(out, [])

    def test_no_content_returns_empty_list(self):
        c = SKHynixCrawler(driver=None)
        out = c._parse_sk_hynix("SK하이닉스", _soup("<div/>"), "<div/>")
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
