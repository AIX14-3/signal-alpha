"""잡코리아 목록 '등록' 텍스트 → 게시일 파서(parse_posting_date) 단위테스트.

네트워크/Selenium 없이 순수 문자열 매칭만. anchor 를 고정해 결정론적으로 검증한다.
핵심: 같은 카드의 마감일('~07/08(수)')을 게시일로 오인하지 않는다('등록' 앵커).
"""
from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.collectors.hiring.sites.jobkorea import parse_posting_date

_KST = ZoneInfo("Asia/Seoul")
_ANCHOR = datetime(2026, 6, 25, 10, 0, tzinfo=_KST)  # 수집 시각 고정


def _p(text):
    return parse_posting_date(text, anchor=_ANCHOR)


class ParsePostingDateTest(unittest.TestCase):
    def test_relative_days(self):
        self.assertEqual(_p("3일 전 등록"), "2026-06-22")

    def test_relative_hours_is_today(self):
        # 24시간 내 등록 → 오늘. 같은 카드의 마감(~07/23)은 무시.
        self.assertEqual(_p("21시간 전 등록 ~07/23(수)"), "2026-06-25")

    def test_relative_minutes_and_now(self):
        self.assertEqual(_p("30분 전 등록"), "2026-06-25")
        self.assertEqual(_p("방금 등록"), "2026-06-25")

    def test_absolute_ignores_deadline(self):
        # 등록(06/16)과 마감(07/08)이 한 줄에 — 반드시 등록일만.
        self.assertEqual(_p("06/16(화)등록 ~07/08(수)"), "2026-06-16")

    def test_absolute_when_deadline_comes_first(self):
        # 마감이 먼저 나오는 레이아웃에서도 '등록' 앵커라 06/16을 집는다.
        self.assertEqual(_p("마감 ~07/08(수)  06/16(화)등록"), "2026-06-16")

    def test_year_inference_for_year_end(self):
        # 수집은 6월인데 등록월이 12월 → 작년(2025).
        self.assertEqual(_p("12/20(금)등록"), "2025-12-20")

    def test_deadline_only_returns_none(self):
        self.assertIsNone(_p("~07/08(수) 마감"))   # '등록' 없음 → None(수집시각 폴백)

    def test_invalid_date_returns_none(self):
        self.assertIsNone(_p("02/30(토)등록"))

    def test_empty_or_none(self):
        self.assertIsNone(_p(""))
        self.assertIsNone(_p(None))


if __name__ == "__main__":
    unittest.main()
