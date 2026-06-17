"""HYBE / SM엔터 GreetingHR 공유 파서 단위 테스트 (#175 / #241·#242).

- /ko/career(HYBE)·/ko/sm-apply(SM)의 GreetingHR opening 파싱(동일 구조, 파서 1개 공유)
- 공고 링크 a[href*="/ko/o/<id>"], 제목 [class*="OpeningListItemTitle"]
- 상세 URL {base}/ko/o/<id>, 비-opening 네비 링크는 제외
- CollectorResult 로 raw_payload·source_label 보존(4b reparse 호환)
"""

from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app.collectors.hiring.base_collector import CollectorResult
from app.collectors.hiring.sites.company.hybe_sm import (
    _HYBE_BASE,
    _SM_BASE,
    HybeCrawler,
    SMCrawler,
)

_FIXDIR = Path(__file__).resolve().parents[1] / "fixtures" / "hiring"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _load(name: str) -> str:
    return (_FIXDIR / name).read_text(encoding="utf-8")


class HybeParserTest(unittest.TestCase):
    def setUp(self):
        self.c = HybeCrawler(driver=None)
        raw = _load("HYBE.html")
        self.results = self.c._parse_greetinghr("HYBE", _soup(raw), _HYBE_BASE, raw)

    def test_three_openings(self):
        self.assertEqual(len(self.results), 3)
        self.assertTrue(all(isinstance(r, CollectorResult) for r in self.results))

    def test_titles(self):
        titles = [r.data["job_title"] for r in self.results]
        self.assertIn("[KOZ Entertainment] 레이블경영관리", titles)
        self.assertIn("[BIGHIT MUSIC] 미디어프로모션(TXT 담당)", titles)

    def test_detail_url(self):
        first = next(r for r in self.results if "레이블경영관리" in r.data["job_title"])
        self.assertEqual(
            first.data["source_url"], "https://careers.hybecorp.com/ko/o/223329"
        )

    def test_nav_link_excluded(self):
        urls = [r.data["source_url"] for r in self.results]
        self.assertTrue(all("/ko/o/" in u for u in urls))
        self.assertNotIn("https://careers.hybecorp.com/ko/career", urls)

    def test_raw_and_label(self):
        for r in self.results:
            self.assertEqual(r.source_label, "HYBE_CAREERS")
            self.assertTrue(r.raw_payload)


class SMParserTest(unittest.TestCase):
    def setUp(self):
        self.c = SMCrawler(driver=None)
        raw = _load("SM.html")
        self.results = self.c._parse_greetinghr("SM엔터테인먼트", _soup(raw), _SM_BASE, raw)

    def test_three_openings(self):
        self.assertEqual(len(self.results), 3)

    def test_detail_url_and_label(self):
        first = next(r for r in self.results if "트레이닝" in r.data["job_title"])
        self.assertEqual(
            first.data["source_url"],
            "https://recruit.smentertainment.com/ko/o/223178",
        )
        self.assertEqual(first.source_label, "SM_CAREERS")


class GreetingHREmptyTest(unittest.TestCase):
    def test_no_openings_returns_empty(self):
        html = '<div class="opening-area"><nav><a href="/ko/career">홈</a></nav></div>'
        c = HybeCrawler(driver=None)
        self.assertEqual(c._parse_greetinghr("HYBE", _soup(html), _HYBE_BASE, html), [])


if __name__ == "__main__":
    unittest.main()
