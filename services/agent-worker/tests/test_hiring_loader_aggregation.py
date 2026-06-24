"""Unit tests for _aggregate_to_daily in HiringEvidenceLoader.

핵심 검증:
- base_collector가 저장하는 job_count=1 행들이 일별 합산으로 변환되는지
- indicators.py가 의미 있는 momentum_pct를 계산할 수 있는 데이터 형태인지
- change_pct가 전일 대비로 계산되는지
- 키워드 서피싱(job_title, tech_stack) 필드가 집계 후에도 보존되는지
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from app.evidence_loaders.hiring_loader import _aggregate_to_daily, _drop_warming_up


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

    def test_ocr_skills_union_per_day(self):
        """당일 공고들의 ocr_skills가 중복 제거 후 합집합으로 보존된다 (스킬 스코어 입력)."""
        rows = [
            {**_posting("2026-06-10"), "ocr_skills": ["Python", "Kubernetes"]},
            {**_posting("2026-06-10"), "ocr_skills": ["Python", "AWS"]},
        ]
        result = _aggregate_to_daily(rows)
        skills = result[0]["ocr_skills"]
        self.assertEqual(set(skills), {"Python", "Kubernetes", "AWS"})
        self.assertEqual(skills.count("Python"), 1)  # 중복 제거

    def test_seasonal_factor_preserved(self):
        rows = [_posting("2026-06-10", seasonal_factor=1.2)]
        result = _aggregate_to_daily(rows)
        self.assertAlmostEqual(result[0]["seasonal_factor"], 1.2)


class WarmingUpGuardTest(unittest.TestCase):
    """Warming-up 가드(#290): 소스 최초 등장·장기(>5일) 공백 후 catch-up 행 제외(소스 배제·종목 유지)."""

    @staticmethod
    def _p(date_str: str, source_key: str, n: int = 1) -> list[dict]:
        return [
            {"observed_date": date_str, "source_key": source_key, "job_count": 1}
            for _ in range(n)
        ]

    def test_first_appearance_all_rows_excluded(self):
        """단일 소스 최초 등장 164행 → (소스,날짜) 단위 판정으로 전부 제외(HYBE형)."""
        rows = self._p("2026-06-18", "HYBE_CAREERS", n=164)
        self.assertEqual(_drop_warming_up(rows), [])

    def test_source_warms_up_after_first_day(self):
        """연속일: 최초일만 제외, 다음 날부터 정상 복귀(영구 갇힘 없음)."""
        rows: list[dict] = []
        base = date(2026, 6, 10)
        for i in range(6):  # 6/10 ~ 6/15 연속
            rows += self._p((base + timedelta(days=i)).isoformat(), "SARAMIN")
        kept = {r["observed_date"] for r in _drop_warming_up(rows)}
        self.assertNotIn("2026-06-10", kept)  # 최초 등장 → 제외
        self.assertIn("2026-06-11", kept)     # 직전일 이력 → 유지
        self.assertIn("2026-06-15", kept)

    def test_mixed_sources_only_warming_source_excluded(self):
        """포털(연속·정상) + 공식(당일 최초 164) → 같은 날 공식만 제외, 포털 유지."""
        rows: list[dict] = []
        base = date(2026, 6, 14)
        for i in range(5):  # 포털 6/14~6/18 연속
            rows += self._p((base + timedelta(days=i)).isoformat(), "PORTAL_UNKNOWN")
        rows += self._p("2026-06-18", "HYBE_CAREERS", n=164)  # 공식 첫 등장
        kept_618 = [r for r in _drop_warming_up(rows) if r["observed_date"] == "2026-06-18"]
        self.assertEqual(len(kept_618), 1)  # 포털 1건만 남고 공식 164 제외
        self.assertEqual(kept_618[0]["source_key"], "PORTAL_UNKNOWN")

    def test_gap_over_5days_reappearance_excluded(self):
        """9일 공백 후 재개 → warming-up(제외)."""
        rows = self._p("2026-06-01", "X") + self._p("2026-06-10", "X")
        kept = {r["observed_date"] for r in _drop_warming_up(rows)}
        self.assertNotIn("2026-06-01", kept)
        self.assertNotIn("2026-06-10", kept)

    def test_gap_within_5days_kept(self):
        """4일 간격 → 정상 변동(유지)."""
        rows = self._p("2026-06-01", "X") + self._p("2026-06-05", "X")
        kept = {r["observed_date"] for r in _drop_warming_up(rows)}
        self.assertIn("2026-06-05", kept)

    def test_null_source_treated_as_portal_unknown(self):
        """source_key 없는 행(포털)도 PORTAL_UNKNOWN으로 묶여 판정된다."""
        rows = [{"observed_date": "2026-06-10", "job_count": 1}] * 2  # source_key 없음
        # 단일 날짜 최초 등장 → 제외
        self.assertEqual(_drop_warming_up(rows), [])


if __name__ == "__main__":
    unittest.main()
