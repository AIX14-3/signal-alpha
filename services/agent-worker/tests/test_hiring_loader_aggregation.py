"""Unit tests for _aggregate_to_daily in HiringEvidenceLoader.

핵심 검증:
- base_collector가 저장하는 job_count=1 행들이 일별 합산으로 변환되는지
- indicators.py가 의미 있는 momentum_pct를 계산할 수 있는 데이터 형태인지
- change_pct가 전일 대비로 계산되는지
- 키워드 서피싱(job_title, tech_stack) 필드가 집계 후에도 보존되는지
"""
from __future__ import annotations

import unittest

from app.evidence_loaders.hiring_loader import _aggregate_to_daily


def _posting(observed_date: str, *, job_count: int = 1,
             job_title: str | None = None, tech_stack: list | None = None,
             seasonal_factor: float = 1.0) -> dict:
    return {
        "observed_date": observed_date,
        "job_count": job_count,
        "previous_job_count": None,
        "change_pct": None,
        "seasonal_factor": seasonal_factor,
        "source_url": None,
        "job_title": job_title,
        "tech_stack": tech_stack or [],
    }


class AggregateEmptyTest(unittest.TestCase):
    def test_empty_input_returns_empty(self):
        self.assertEqual(_aggregate_to_daily([]), [])

    def test_row_without_date_is_skipped(self):
        rows = [{"job_count": 1, "observed_date": None}]
        self.assertEqual(_aggregate_to_daily(rows), [])


class AggregateSumTest(unittest.TestCase):
    def test_same_day_postings_are_summed(self):
        """job_count=1 행 5개가 같은 날이면 job_count=5로 합산."""
        rows = [_posting("2026-06-10") for _ in range(5)]
        result = _aggregate_to_daily(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["job_count"], 5)
        self.assertEqual(result[0]["observed_date"], "2026-06-10")

    def test_different_days_produce_separate_rows(self):
        rows = [_posting("2026-06-10"), _posting("2026-06-11"), _posting("2026-06-11")]
        result = _aggregate_to_daily(rows)
        self.assertEqual(len(result), 2)
        by_date = {r["observed_date"]: r for r in result}
        self.assertEqual(by_date["2026-06-10"]["job_count"], 1)
        self.assertEqual(by_date["2026-06-11"]["job_count"], 2)

    def test_result_is_newest_first(self):
        """반환 순서는 최신 날짜 우선 (loader 관례)."""
        rows = [_posting("2026-06-09"), _posting("2026-06-10"), _posting("2026-06-11")]
        result = _aggregate_to_daily(rows)
        dates = [r["observed_date"] for r in result]
        self.assertEqual(dates, sorted(dates, reverse=True))


class AggregateChangePctTest(unittest.TestCase):
    def test_first_day_has_no_change_pct(self):
        rows = [_posting("2026-06-10")]
        result = _aggregate_to_daily(rows)
        self.assertIsNone(result[0]["change_pct"])

    def test_change_pct_is_day_over_day(self):
        """전일 3건 → 당일 6건 = +100%."""
        rows = [_posting("2026-06-10") for _ in range(3)] + \
               [_posting("2026-06-11") for _ in range(6)]
        result = _aggregate_to_daily(rows)
        by_date = {r["observed_date"]: r for r in result}
        self.assertIsNone(by_date["2026-06-10"]["change_pct"])
        self.assertAlmostEqual(by_date["2026-06-11"]["change_pct"], 100.0)

    def test_decrease_is_negative_change_pct(self):
        """전일 10건 → 당일 5건 = -50%."""
        rows = [_posting("2026-06-10") for _ in range(10)] + \
               [_posting("2026-06-11") for _ in range(5)]
        result = _aggregate_to_daily(rows)
        by_date = {r["observed_date"]: r for r in result}
        self.assertAlmostEqual(by_date["2026-06-11"]["change_pct"], -50.0)


class AggregateMomentumReadinessTest(unittest.TestCase):
    """indicators.py가 meaningful momentum을 계산할 수 있는지 검증.

    job_count=1 그대로면 recent_avg = prior_avg = 1.0 → momentum=0.
    집계 후에는 일별 합산값이 달라지므로 momentum이 0이 아니다.
    """

    def test_growing_hiring_produces_higher_recent_avg(self):
        """초반 45일 3건/일, 후반 45일 9건/일 → recent_avg > prior_avg."""
        rows = []
        # prior window: 2026-01-01 ~ 2026-02-14 (45일, 3건/일)
        from datetime import date, timedelta
        base = date(2026, 1, 1)
        for i in range(45):
            d = (base + timedelta(days=i)).isoformat()
            rows.extend(_posting(d) for _ in range(3))
        # recent window: 2026-02-15 ~ 2026-03-31 (45일, 9건/일)
        for i in range(45):
            d = (base + timedelta(days=45 + i)).isoformat()
            rows.extend(_posting(d) for _ in range(9))

        result = _aggregate_to_daily(rows)
        by_date = {r["observed_date"]: r for r in result}

        recent_avg = sum(
            v["job_count"] for k, v in by_date.items() if k >= "2026-02-15"
        ) / 45
        prior_avg = sum(
            v["job_count"] for k, v in by_date.items() if k < "2026-02-15"
        ) / 45

        self.assertAlmostEqual(recent_avg, 9.0)
        self.assertAlmostEqual(prior_avg, 3.0)
        self.assertGreater(recent_avg, prior_avg)


class AggregateDescriptiveFieldsTest(unittest.TestCase):
    def test_job_titles_preserved_per_day(self):
        """당일 공고들의 job_title 중 첫 번째가 대표값으로 보존된다."""
        rows = [
            _posting("2026-06-10", job_title="백엔드 개발자"),
            _posting("2026-06-10", job_title="프론트엔드 개발자"),
        ]
        result = _aggregate_to_daily(rows)
        self.assertEqual(result[0]["job_title"], "백엔드 개발자")

    def test_tech_stacks_union_per_day(self):
        """당일 공고들의 tech_stack이 중복 제거 후 합집합으로 보존된다."""
        rows = [
            _posting("2026-06-10", tech_stack=["Python", "Django"]),
            _posting("2026-06-10", tech_stack=["Python", "FastAPI"]),
        ]
        result = _aggregate_to_daily(rows)
        techs = result[0]["tech_stack"]
        self.assertIn("Python", techs)
        self.assertIn("Django", techs)
        self.assertIn("FastAPI", techs)
        self.assertEqual(techs.count("Python"), 1)  # 중복 제거

    def test_seasonal_factor_preserved(self):
        rows = [_posting("2026-06-10", seasonal_factor=1.2)]
        result = _aggregate_to_daily(rows)
        self.assertAlmostEqual(result[0]["seasonal_factor"], 1.2)


if __name__ == "__main__":
    unittest.main()
