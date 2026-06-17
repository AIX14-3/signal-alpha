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

    def test_menu_links_filtered_real_job_kept(self):
        html = """
            <a href="/careers/welfare">복지제도</a>
            <a href="/careers/faq">FAQs</a>
            <a href="/careers/diversity">다양성포용</a>
            <a href="/careers/apply/123">경력사원 채용(AI 보안)</a>
        """
        results = self.c._parse_samsung_bio(
            "삼성바이오로직스", "https://samsungbiologics.com", _soup(html), "<html>RAW</html>"
        )
        titles = [r.data["job_title"] for r in results]
        self.assertIn("경력사원 채용(AI 보안)", titles)   # 실공고 통과
        self.assertNotIn("복지제도", titles)               # 메뉴 제외
        self.assertNotIn("FAQs", titles)
        self.assertNotIn("다양성포용", titles)

    def test_returns_collector_result_with_raw_payload(self):
        html = '<a href="/careers/apply/1">데이터 엔지니어 채용</a>'
        results = self.c._parse_samsung_bio(
            "삼성바이오로직스", "https://samsungbiologics.com", _soup(html), "<html>RAW</html>"
        )
        self.assertTrue(results)
        self.assertTrue(all(isinstance(r, CollectorResult) for r in results))
        self.assertEqual(results[0].raw_payload, "<html>RAW</html>")
        self.assertEqual(results[0].source_label, "SAMSUNG_BIO_CAREERS")
        self.assertEqual(results[0].data["job_title"], "데이터 엔지니어 채용")

    def test_no_jobs_falls_back_to_guide_page(self):
        results = self.c._parse_samsung_bio(
            "삼성바이오로직스", "https://samsungbiologics.com", _soup("<div>없음</div>"), "RAW"
        )
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], CollectorResult)
        self.assertIn("채용 안내", results[0].data["job_title"])
        self.assertEqual(results[0].raw_payload, "RAW")   # fallback 도 원본 보존


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
