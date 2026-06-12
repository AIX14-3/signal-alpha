"""Unit tests for compute_baseline (pure seasonal-factor logic).

No DB / API: validates the quarterly-index math, small-sample defenses, and
period parsing that DataLabBaselineCollector relies on.
"""

import unittest

from app.baseline.hiring_baseline_builder import BaselineStats, compute_baseline


def _two_years(q_values: dict[int, float]) -> list[tuple[str, float]]:
    """Build 24 monthly points (2023–2024) where every month in quarter q
    carries q_values[q]. Equal months per quarter → overall mean = mean(q_values).
    """
    points: list[tuple[str, float]] = []
    for year in (2023, 2024):
        for month in range(1, 13):
            quarter = (month - 1) // 3 + 1
            points.append((f"{year}-{month:02d}-01", q_values[quarter]))
    return points


class TestComputeBaseline(unittest.TestCase):

    def test_doc_example_seasonal_factors(self):
        """Doc example: Q1=120,Q2=80,Q3=100,Q4=100, overall=100 → factors 1.2/0.8/1.0/1.0."""
        stats = compute_baseline(_two_years({1: 120.0, 2: 80.0, 3: 100.0, 4: 100.0}))
        self.assertIsInstance(stats, BaselineStats)
        self.assertAlmostEqual(stats.avg_search_volume, 100.0, places=2)
        self.assertAlmostEqual(stats.q1_factor, 1.2, places=3)
        self.assertAlmostEqual(stats.q2_factor, 0.8, places=3)
        self.assertAlmostEqual(stats.q3_factor, 1.0, places=3)
        self.assertAlmostEqual(stats.q4_factor, 1.0, places=3)
        self.assertEqual(stats.observations, 24)
        self.assertEqual(stats.data_start_date, "2023-01-01")
        self.assertEqual(stats.data_end_date, "2024-12-01")

    def test_factor_helper_accessor(self):
        stats = compute_baseline(_two_years({1: 120.0, 2: 80.0, 3: 100.0, 4: 100.0}))
        self.assertAlmostEqual(stats.factor(1), 1.2, places=3)
        self.assertAlmostEqual(stats.factor(2), 0.8, places=3)

    def test_empty_input_neutral_baseline(self):
        stats = compute_baseline([])
        self.assertEqual(stats.avg_search_volume, 0.0)
        self.assertEqual(
            (stats.q1_factor, stats.q2_factor, stats.q3_factor, stats.q4_factor),
            (1.0, 1.0, 1.0, 1.0),
        )
        self.assertEqual(stats.observations, 0)
        self.assertIsNone(stats.data_start_date)
        self.assertIsNone(stats.data_end_date)

    def test_all_zero_ratios_neutral_factors(self):
        """Overall mean 0 → factors must default to 1.0 (no divide-by-zero)."""
        stats = compute_baseline(_two_years({1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}))
        self.assertEqual(stats.avg_search_volume, 0.0)
        self.assertEqual(
            (stats.q1_factor, stats.q2_factor, stats.q3_factor, stats.q4_factor),
            (1.0, 1.0, 1.0, 1.0),
        )
        self.assertEqual(stats.observations, 24)

    def test_under_observed_quarter_keeps_neutral_factor(self):
        """A quarter with fewer than min_months_per_quarter points stays at 1.0."""
        points = [
            ("2024-01-01", 120.0), ("2024-02-01", 120.0), ("2024-03-01", 120.0),  # Q1: 3 obs
            ("2024-04-01", 50.0),  # Q2: 1 obs only → under-observed
            ("2024-07-01", 100.0), ("2024-08-01", 100.0),  # Q3: 2 obs
        ]
        stats = compute_baseline(points, min_months_per_quarter=2)
        self.assertEqual(stats.q2_factor, 1.0)   # under-observed → neutral
        self.assertNotEqual(stats.q1_factor, 1.0)  # well-observed → real factor
        self.assertEqual(stats.q4_factor, 1.0)   # no data → neutral

    def test_year_month_period_format_tolerated(self):
        """Naver sometimes returns 'YYYY-MM' — must still parse to the right quarter."""
        points = [("2024-01", 120.0), ("2024-02", 120.0), ("2024-03", 120.0),
                  ("2024-07", 60.0), ("2024-08", 60.0), ("2024-09", 60.0)]
        stats = compute_baseline(points)
        self.assertEqual(stats.observations, 6)
        self.assertEqual(stats.data_start_date, "2024-01-01")
        # overall mean = 90 → Q1 factor 120/90, Q3 factor 60/90
        self.assertAlmostEqual(stats.q1_factor, round(120 / 90, 3), places=3)
        self.assertAlmostEqual(stats.q3_factor, round(60 / 90, 3), places=3)

    def test_none_ratio_skipped(self):
        points = [("2024-01-01", 100.0), ("2024-02-01", None), ("2024-03-01", 100.0)]
        stats = compute_baseline(points)
        self.assertEqual(stats.observations, 2)


if __name__ == "__main__":
    unittest.main()
