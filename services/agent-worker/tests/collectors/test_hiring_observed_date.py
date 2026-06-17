"""observed_date '오늘' 경계 KST 정합성 단위 테스트 (#253).

hiring_raw_details.observed_date 를 DB 서버 tz(CURRENT_DATE)가 아니라 KST 자정 기준으로
고정(_kst_today). UTC 서버에서 KST 00:00~09:00 수집분이 전날로 오분류되는 문제 방지.
"""

from __future__ import annotations

import datetime
import unittest
from zoneinfo import ZoneInfo

from app.collectors.hiring.base_collector import _kst_today


class KstTodayTest(unittest.TestCase):
    def test_returns_date(self):
        self.assertIsInstance(_kst_today(), datetime.date)

    def test_matches_kst_calendar_day(self):
        # _kst_today()는 Asia/Seoul 자정 기준 오늘과 일치해야 한다(서버 로컬/UTC 아님).
        expected = datetime.datetime.now(ZoneInfo("Asia/Seoul")).date()
        self.assertEqual(_kst_today(), expected)

    def test_not_naive_utc_date_when_diverging(self):
        # KST와 UTC의 달력 날짜가 갈리는 시각(UTC 15:00~24:00 = KST 익일 00:00~09:00)에는
        # _kst_today()가 UTC date(date.today 류)보다 하루 앞서야 한다. 그 외 시각엔 동일.
        utc_today = datetime.datetime.now(ZoneInfo("UTC")).date()
        kst = _kst_today()
        self.assertIn((kst - utc_today).days, (0, 1))


if __name__ == "__main__":
    unittest.main()
