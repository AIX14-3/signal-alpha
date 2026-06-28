"""list_latest_source_results_for_stock: last-known 재사용(소스별 차등 유효기간) 쿼리 계약."""

import unittest
from datetime import date

from signal_alpha_data_access.repositories.analysis import AnalysisRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return []


class LastKnownReuseQueryTest(unittest.IsolatedAsyncioTestCase):
    async def test_windowed_lookback_defaults(self):
        connection = FakeConnection()
        repository = AnalysisRepository(connection)

        await repository.list_latest_source_results_for_stock(
            stock_id=7, analysis_date=date(2026, 6, 28)
        )

        sql, args = connection.calls[0]
        # 정확 일치(=)가 아니라 기준일 이하 + 소스별 유효기간 하한.
        self.assertIn("analysis_date <= $2::DATE", sql)
        self.assertIn("CASE", sql)
        self.assertIn("data_age_days", sql)
        # 최신 날짜 우선 정렬(재사용 시 가장 최근 직전 결과).
        self.assertIn("analysis_results.analysis_date DESC", sql)
        # 기본 윈도: long=30(DART/PATENT/REPORT), short=7(HIRING/DATALAB/PRICE).
        self.assertEqual(args, (7, date(2026, 6, 28), 30, 7))

    async def test_custom_windows_passed_through(self):
        connection = FakeConnection()
        repository = AnalysisRepository(connection)

        await repository.list_latest_source_results_for_stock(
            stock_id=7,
            analysis_date=date(2026, 6, 28),
            long_window_days=14,
            short_window_days=3,
        )

        _sql, args = connection.calls[0]
        self.assertEqual(args, (7, date(2026, 6, 28), 14, 3))


if __name__ == "__main__":
    unittest.main()
