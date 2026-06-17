"""KRAFTON 자체 DOM 채용관 파서 단위 테스트 (#175 / #240).

- careers/jobs/ 의 SSR div.RecruitItem 파싱
- 직무명 h3.RecruitItemTitle-title, 상세 링크 a.RecruitItemTitle-link(recruit-detail?job=N)
- 북마크 앵커(a.RecruitItem-mark, href="#;")는 직무 링크로 수집되지 않음
- 메타(RecruitItemMetaCategory-text)는 job_description 에 보존
- CollectorResult 로 raw_payload·source_label 보존(4b reparse 호환)
"""

from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app.collectors.hiring.base_collector import CollectorResult
from app.collectors.hiring.sites.company.krafton import KraftonCrawler

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hiring" / "KRAFTON.html"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _load_fixture() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


class KraftonParserTest(unittest.TestCase):
    def setUp(self):
        self.c = KraftonCrawler(driver=None)
        raw = _load_fixture()
        self.results = self.c._parse_krafton("크래프톤", _soup(raw), raw)

    def test_returns_collector_result_per_item(self):
        self.assertEqual(len(self.results), 2)
        self.assertTrue(all(isinstance(r, CollectorResult) for r in self.results))

    def test_titles(self):
        titles = [r.data["job_title"] for r in self.results]
        self.assertIn("[KRAFTON JUNGLE Div.] 정글 교육과정 운영지원 Jr. 팀원 (3~6년 / 계약직)", titles)
        self.assertIn("[Publishing Platform Div.] Sr. Mobile App Developer (7년 이상 / 계약직)", titles)

    def test_detail_url(self):
        first = next(r for r in self.results if "정글" in r.data["job_title"])
        url = first.data["source_url"]
        self.assertTrue(url.startswith("https://www.krafton.com/careers/recruit-detail/"))
        self.assertIn("job=4760", url)

    def test_bookmark_anchor_not_used_as_url(self):
        """북마크 앵커(href='#;')가 source_url 로 잡히지 않음."""
        for r in self.results:
            self.assertNotIn("#;", r.data["source_url"])
            self.assertIn("recruit-detail", r.data["source_url"])

    def test_meta_into_description(self):
        first = next(r for r in self.results if "정글" in r.data["job_title"])
        desc = first.data["job_description"]
        self.assertIn("Management Supporting", desc)
        self.assertIn("Seoul", desc)

    def test_raw_payload_and_label_preserved(self):
        raw = _load_fixture()
        for r in self.results:
            self.assertEqual(r.raw_payload, raw)
            self.assertEqual(r.source_label, "KRAFTON_CAREERS")


class KraftonEmptyPageTest(unittest.TestCase):
    def test_empty_list_returns_empty(self):
        html = '<div class="RecruitList"><ul class="RecruitList-list"></ul></div>'
        c = KraftonCrawler(driver=None)
        self.assertEqual(c._parse_krafton("크래프톤", _soup(html), html), [])

    def test_no_content_returns_empty(self):
        c = KraftonCrawler(driver=None)
        self.assertEqual(c._parse_krafton("크래프톤", _soup("<div/>"), "<div/>"), [])


if __name__ == "__main__":
    unittest.main()
