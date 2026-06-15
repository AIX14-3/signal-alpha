"""Unit tests for hiring job-function classification + sector-demand aggregation (C4)."""

import unittest
from datetime import date, timedelta

from app.analyzers.hiring.job_functions import (
    aggregate_sector_demand,
    classify_job_function,
)

AS_OF = date(2026, 6, 11)
LOOKBACK = 90


def _row(stock_id, keyword, days_ago, job_count=1):
    return {
        "stock_id": stock_id,
        "keyword": keyword,
        "job_count": job_count,
        "observed_date": (AS_OF - timedelta(days=days_ago)).isoformat(),
    }


class ClassifyTests(unittest.TestCase):
    def test_korean_and_english_titles(self):
        self.assertEqual(classify_job_function("백엔드 개발자"), "ENGINEER")
        self.assertEqual(classify_job_function("Senior Software Engineer"), "ENGINEER")
        self.assertEqual(classify_job_function("해외영업 담당"), "SALES")
        self.assertEqual(classify_job_function("생산기술 엔지니어"), "ENGINEER")  # first rule wins
        self.assertEqual(classify_job_function("품질관리"), "MANUFACTURING")
        self.assertEqual(classify_job_function("브랜드 마케터"), "MARKETING")

    def test_unmatched_is_none(self):
        self.assertIsNone(classify_job_function("점성술사"))
        self.assertIsNone(classify_job_function(None))
        self.assertIsNone(classify_job_function(""))


class AggregateSectorDemandTests(unittest.TestCase):
    def test_no_weights_returns_none(self):
        demand = aggregate_sector_demand(
            [_row(2, "개발자", 5)], as_of=AS_OF, lookback_days=LOOKBACK, function_weights={}
        )
        self.assertIsNone(demand.momentum_pct)
        self.assertEqual(demand.coverage_weight, 0.0)

    def test_peer_rising_demand_is_positive(self):
        # Peer engineer postings: 1 in prior half, 3 in recent half → +200% momentum.
        rows = [
            _row(2, "개발자", 5), _row(2, "백엔드 개발", 10), _row(3, "소프트웨어 엔지니어", 15),
            _row(2, "개발자", 70),
        ]
        demand = aggregate_sector_demand(
            rows, as_of=AS_OF, lookback_days=LOOKBACK, function_weights={"ENGINEER": 1.0}
        )
        self.assertIsNotNone(demand.momentum_pct)
        self.assertAlmostEqual(demand.momentum_pct, 2.0)
        self.assertEqual(demand.coverage_weight, 1.0)

    def test_target_stock_is_excluded_from_peer_signal(self):
        # Only the target's own rows exist → no peer data → momentum None.
        rows = [_row(1, "개발자", 5), _row(1, "개발자", 70)]
        demand = aggregate_sector_demand(
            rows,
            as_of=AS_OF,
            lookback_days=LOOKBACK,
            function_weights={"ENGINEER": 1.0},
            target_stock_id=1,
        )
        self.assertIsNone(demand.momentum_pct)

    def test_weighted_blend_across_functions(self):
        rows = [
            # ENGINEER: prior 1 → recent 2 (+100%)
            _row(2, "개발자", 5), _row(2, "개발자", 10), _row(3, "개발자", 70),
            # SALES: prior 2 → recent 1 (-50%)
            _row(2, "영업", 5), _row(2, "영업", 70), _row(3, "영업", 75),
        ]
        demand = aggregate_sector_demand(
            rows,
            as_of=AS_OF,
            lookback_days=LOOKBACK,
            function_weights={"ENGINEER": 3.0, "SALES": 1.0},
        )
        # (1.0*3 + -0.5*1) / 4 = 0.625
        self.assertAlmostEqual(demand.momentum_pct, 0.625)
        self.assertEqual(demand.coverage_weight, 4.0)


if __name__ == "__main__":
    unittest.main()
