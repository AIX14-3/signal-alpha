"""삼성바이오 파서 CollectorResult 전환 + 휴리스틱 단위 테스트 (Phase 4b / #175 1차).

- 안내 메뉴(복지제도·FAQs) 휴리스틱 제외, 실공고는 통과
- 각 레코드를 CollectorResult 로 감싸 raw_payload·source_label 보존
- _fetch_soup 가 (soup, raw_html) 튜플 반환
"""

from __future__ import annotations

import unittest
from unittest import mock

from bs4 import BeautifulSoup

from app.collectors.hiring.base_collector import CollectorResult
from app.collectors.hiring.sites.company.simple_sites import SimpleSiteCrawler


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


class SamsungBioParserTest(unittest.TestCase):
    def setUp(self):
        self.c = SimpleSiteCrawler()           # 무인자 생성(driver=None)
        self.c.source_label = "SAMSUNG_BIO_CAREERS"

    def test_menu_links_not_scraped_only_guide(self):
        """career 링크(메뉴)가 있어도 긁지 않고 '채용 안내' 1건만 반환(공고 미노출 사이트)."""
        html = """
            <a href="/careers/working-with-us/benefits">복지제도</a>
            <a href="/careers/value">인재상</a>
            <a href="/careers/talent-pool">인재 POOL 등록</a>
            <a href="/careers/apply/how-to-apply">채용안내</a>
        """
        results = self.c._parse_samsung_bio(
            "삼성바이오로직스", "https://samsungbiologics.com", _soup(html), "<html>RAW</html>"
        )
        self.assertEqual(len(results), 1)
        title = results[0].data["job_title"]
        self.assertIn("채용 안내", title)
        # 메뉴(복지제도/인재상/인재POOL)가 공고로 둔갑하지 않음
        for menu in ("복지제도", "인재상", "인재 POOL 등록"):
            self.assertNotEqual(title, menu)

    def test_collector_result_preserves_raw_and_label(self):
        results = self.c._parse_samsung_bio(
            "삼성바이오로직스", "https://samsungbiologics.com", _soup("<div/>"), "<html>RAW</html>"
        )
        self.assertTrue(all(isinstance(r, CollectorResult) for r in results))
        self.assertEqual(results[0].raw_payload, "<html>RAW</html>")
        self.assertEqual(results[0].source_label, "SAMSUNG_BIO_CAREERS")

    def test_empty_page_still_one_guide(self):
        results = self.c._parse_samsung_bio(
            "삼성바이오로직스", "https://samsungbiologics.com", _soup("<div>없음</div>"), "RAW"
        )
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], CollectorResult)
        self.assertIn("채용 안내", results[0].data["job_title"])
        self.assertEqual(results[0].raw_payload, "RAW")   # 안내 1건도 원본 보존


class FetchSoupTest(unittest.TestCase):
    def test_returns_soup_and_raw_html(self):
        c = SimpleSiteCrawler()
        with mock.patch("app.collectors.hiring.sites.http.get") as mock_get:
            mock_get.return_value = mock.Mock(text="<html>BODY</html>")
            soup, raw = c._fetch_soup("https://x.test")
        self.assertEqual(raw, "<html>BODY</html>")
        self.assertIsNotNone(soup)

    def test_no_driver_returns_none_tuple(self):
        c = SimpleSiteCrawler(driver=None)
        # requests 실패 + driver 없음 → (None, None)
        with mock.patch("app.collectors.hiring.sites.http.get", side_effect=RuntimeError("net")):
            soup, raw = c._fetch_soup("https://x.test")
        self.assertIsNone(soup)
        self.assertIsNone(raw)


class LegacyParserSignatureTest(unittest.TestCase):
    """한미/스튜디오는 raw_html 인자를 받아도 legacy dict 를 반환(후방호환)."""

    def test_hanmi_still_returns_dict(self):
        c = SimpleSiteCrawler()
        c.source_label = "HANMI_SEMI_CAREERS"
        html = '<table class="board-list"><tr><td>h</td></tr><tr><td><a href="/v/1">공정관리 담당 채용</a></td></tr></table>'
        out = c._parse_hanmi("한미반도체", "https://x", _soup(html), "RAW")
        self.assertTrue(all(isinstance(r, dict) for r in out))


if __name__ == "__main__":
    unittest.main()
