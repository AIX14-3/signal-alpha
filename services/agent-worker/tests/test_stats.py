import unittest
from datetime import date, datetime, timezone

from app.observability.stats import (
    RunStats,
    calculate_run_status,
    failure_rate,
    format_run_summary,
    ingest_success_rate,
    since_to_utc_start,
)


class RateMathTest(unittest.TestCase):
    def test_ingest_success_counts_skips_as_non_failure(self):
        # 100 collected, 70 new + 15 dup/skip = 85% non-error, 15% failure.
        self.assertAlmostEqual(ingest_success_rate(100, 70, 15), 0.85)
        self.assertAlmostEqual(failure_rate(100, 15), 0.15)

    def test_zero_collected_returns_none(self):
        self.assertIsNone(ingest_success_rate(0, 0, 0))
        self.assertIsNone(failure_rate(0, 0))

    def test_all_failed(self):
        self.assertAlmostEqual(ingest_success_rate(10, 0, 0), 0.0)
        self.assertAlmostEqual(failure_rate(10, 10), 1.0)

    def test_all_success(self):
        self.assertAlmostEqual(ingest_success_rate(10, 10, 0), 1.0)
        self.assertAlmostEqual(failure_rate(10, 0), 0.0)


class CalculateRunStatusTest(unittest.TestCase):
    def test_success_when_no_failures_and_has_usable(self):
        self.assertEqual(calculate_run_status(5, 0, 0), "success")
        self.assertEqual(calculate_run_status(0, 5, 0), "success")  # skip-only is success
        self.assertEqual(calculate_run_status(3, 2, 0), "success")

    def test_partial_when_some_usable_and_some_failed(self):
        self.assertEqual(calculate_run_status(3, 0, 1), "partial")
        self.assertEqual(calculate_run_status(0, 3, 1), "partial")

    def test_failed_when_all_records_failed(self):
        # 전건 치명적 실패는 partial 이 아니라 failed.
        self.assertEqual(calculate_run_status(0, 0, 5), "failed")

    def test_failed_when_empty_run(self):
        # 아무것도 수집/적재하지 못한 빈 런은 success 가 아니라 failed.
        self.assertEqual(calculate_run_status(0, 0, 0), "failed")


class RunStatsTest(unittest.TestCase):
    def test_to_dict_exposes_percentages(self):
        stats = RunStats.from_counts(collected=100, inserted=70, skipped=15, failed=15)
        d = stats.to_dict()
        self.assertEqual(d["ingest_success_rate"], 85.0)
        self.assertEqual(d["failure_rate"], 15.0)

    def test_format_summary_human_line(self):
        stats = RunStats.from_counts(collected=100, inserted=70, skipped=15, failed=15)
        line = format_run_summary("HIRING", stats)
        self.assertIn("HIRING", line)
        self.assertIn("적재성공률 85.0%", line)
        self.assertIn("실패율 15.0%", line)

    def test_format_summary_no_data(self):
        stats = RunStats.from_counts(collected=0, inserted=0, skipped=0, failed=0)
        self.assertIn("데이터 없음", format_run_summary("PATENT", stats))


class SinceToUtcTest(unittest.TestCase):
    def test_kst_midnight_maps_to_prev_day_utc_15(self):
        # KST 00:00 == UTC 15:00 of the previous day (KST = UTC+9).
        result = since_to_utc_start("2026-06-16")
        self.assertEqual(result, datetime(2026, 6, 15, 15, 0, tzinfo=timezone.utc))
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_none_and_empty_return_none(self):
        self.assertIsNone(since_to_utc_start(None))
        self.assertIsNone(since_to_utc_start(""))

    def test_accepts_date_object(self):
        self.assertEqual(
            since_to_utc_start(date(2026, 6, 16)),
            datetime(2026, 6, 15, 15, 0, tzinfo=timezone.utc),
        )

    def test_naive_datetime_treated_as_kst(self):
        result = since_to_utc_start(datetime(2026, 6, 16, 0, 0))
        self.assertEqual(result, datetime(2026, 6, 15, 15, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
