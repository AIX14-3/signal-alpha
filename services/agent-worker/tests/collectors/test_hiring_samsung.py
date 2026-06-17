"""삼성전자 채용 안내(guidance-only) 단위 테스트 (#175 / #243).

samsungcareers.com/hr/ 는 관계사 선택형 통합 포털이라 직무 목록 미노출 + 전자 자체 0건.
→ 삼성바이오와 동일하게 '채용 안내' 1건만 CollectorResult 로 반환(raw_payload 보존).
"""

from __future__ import annotations

import unittest

from app.collectors.hiring.base_collector import CollectorResult
from app.collectors.hiring.sites.company.samsung import _PORTAL, SamsungCrawler


class SamsungGuidanceTest(unittest.TestCase):
    def setUp(self):
        self.c = SamsungCrawler(driver=None)
        self.results = self.c._parse_samsung_guidance("삼성전자", "<html>RAW</html>")

    def test_single_guidance_record(self):
        self.assertEqual(len(self.results), 1)
        self.assertIsInstance(self.results[0], CollectorResult)

    def test_title_is_guidance_not_menu(self):
        title = self.results[0].data["job_title"]
        self.assertIn("채용 안내", title)
        # 관계사 타일/메뉴가 공고 제목으로 둔갑하지 않음
        for menu in ("삼성디스플레이", "삼성SDI", "관계사 소개", "DX부문", "DS부문"):
            self.assertNotEqual(title, menu)

    def test_portal_url(self):
        url = self.results[0].data["source_url"]
        self.assertEqual(url, _PORTAL)
        self.assertTrue(url.endswith("/hr/"))  # 끝 슬래시(없으면 404)

    def test_collector_result_preserves_raw_and_label(self):
        self.assertEqual(self.results[0].raw_payload, "<html>RAW</html>")
        self.assertEqual(self.results[0].source_label, "SAMSUNG_CAREERS")

    def test_guidance_even_without_raw(self):
        out = self.c._parse_samsung_guidance("삼성전자", None)
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0].raw_payload)
        self.assertIn("채용 안내", out[0].data["job_title"])


if __name__ == "__main__":
    unittest.main()
