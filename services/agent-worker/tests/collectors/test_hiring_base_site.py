"""BaseSiteCrawler.now_iso() 타임존 단위 테스트 (#120).

모듈 시각 stamp 기준을 KST로 통일 — now_iso()가 tz-aware(+09:00)인지 검증.
(base_collector/multi_source_crawler/observability 가 이미 KST 기준이라 base_site만 맞추는 정리.)
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from app.collectors.hiring.sites.base_site import BaseSiteCrawler


class NowIsoTimezoneTest(unittest.TestCase):
    def test_now_iso_is_kst_aware(self):
        value = BaseSiteCrawler.now_iso()
        dt = datetime.fromisoformat(value)
        # tz-aware 여야 하고(naive 금지), 오프셋이 KST(+09:00)
        self.assertIsNotNone(dt.tzinfo, "now_iso()는 tz-aware 여야 함")
        self.assertEqual(dt.utcoffset(), timedelta(hours=9))

    def test_now_iso_has_offset_suffix(self):
        # isoformat 문자열에 +09:00 오프셋이 포함(UTC의 +00:00 잔재 아님)
        self.assertIn("+09:00", BaseSiteCrawler.now_iso())


if __name__ == "__main__":
    unittest.main()
