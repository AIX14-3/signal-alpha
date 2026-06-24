import unittest
from datetime import date, timedelta

from app.analyzers.config import HiringRuleConfig
from app.analyzers.hiring import HiringAnalyzer
from app.schemas.evidence import RawEvidence

AS_OF = date(2026, 6, 11)
CONFIG = HiringRuleConfig(
    lookback_days=90,
    min_observations=3,
    momentum_threshold=0.1,
    stale_days=45,
    momentum_weight=0.6,
    change_weight=0.4,
    positive_threshold=0.2,
    negative_threshold=-0.2,
)


def _row(days_ago, job_count, *, change_pct=None, seasonal_factor=1.0):
    return {
        "keyword": "k",
        "job_category": "dev",
        "job_count": job_count,
        "previous_job_count": None,
        "change_pct": change_pct,
        "observed_date": (AS_OF - timedelta(days=days_ago)).isoformat(),
        "seasonal_factor": seasonal_factor,
    }


def _evidence(rows, *, sector_demand=None):
    metadata = {"rows": rows, "as_of": AS_OF.isoformat(), "lookback_days": 90}
    if sector_demand is not None:
        metadata["sector_demand"] = sector_demand
    return [
        RawEvidence(
            source="HIRING",
            stock_code="005930",
            title="t",
            content="",
            metadata=metadata,
        )
    ]


class HiringAnalyzerTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_data_is_failed(self):
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence([]))
        self.assertEqual(result.data_status, "failed")
        self.assertIn("no_data", result.risk_flags)

    async def test_hiring_expansion_is_positive(self):
        # prior window needs >= min_prior_observations (5) so the small-sample
        # guard does not suppress the (genuine) momentum.
        rows = [
            _row(5, 150, change_pct=20),
            _row(10, 150, change_pct=20),
            _row(15, 150, change_pct=20),
            _row(55, 100), _row(60, 100), _row(65, 100), _row(70, 100), _row(75, 100),
        ]
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "positive")
        # graded (tanh) scoring no longer saturates to 1.0; still a strong positive.
        self.assertGreater(result.score, 0.6)
        self.assertLess(result.score, 1.0)
        self.assertNotIn("low_base", result.risk_flags)

    async def test_hiring_contraction_is_negative(self):
        rows = [
            _row(5, 100, change_pct=-20),
            _row(10, 100, change_pct=-20),
            _row(15, 100, change_pct=-20),
            _row(55, 200), _row(60, 200), _row(65, 200), _row(70, 200), _row(75, 200),
        ]
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "negative")

    async def test_low_base_guard_suppresses_momentum(self):
        """Tiny prior window → momentum/change suppressed, low_base flagged."""
        rows = [
            _row(5, 8, change_pct=300),
            _row(10, 8, change_pct=300),
            _row(15, 8, change_pct=300),
            _row(60, 2),  # only 1 prior observation
        ]
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertIn("low_base", result.risk_flags)
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.direction, "neutral")

    async def test_seasonal_adjustment_unmasks_offseason_growth(self):
        """Raw counts look flat, but de-seasonalizing by quarter factor reveals
        that the recent (low-season) level is actually elevated → positive."""
        # AS_OF = 2026-06-11 → recent rows in Q2 (factor 0.5), prior in Q1 (factor 1.5).
        # Raw flat 100 vs 100 → no momentum. Adjusted: recent 100/0.5=200,
        # prior 100/1.5=66.7 → +200% momentum.
        rows = [
            _row(5, 100, seasonal_factor=0.5),
            _row(10, 100, seasonal_factor=0.5),
            _row(15, 100, seasonal_factor=0.5),
            _row(55, 100, seasonal_factor=1.5), _row(60, 100, seasonal_factor=1.5),
            _row(65, 100, seasonal_factor=1.5), _row(70, 100, seasonal_factor=1.5),
            _row(75, 100, seasonal_factor=1.5),
        ]
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "positive")
        self.assertNotIn("low_base", result.risk_flags)


    async def test_sector_demand_lifts_score_independent_of_low_base(self):
        """Own momentum is low_base-suppressed, yet peer sector demand still scores —
        the sector component is independent of this stock's sample size."""
        rows = [
            _row(5, 8, change_pct=300), _row(10, 8), _row(15, 8),
            _row(60, 2),  # 1 prior obs → low_base suppresses own momentum/change
        ]
        sector = {"momentum_pct": 0.5, "coverage_weight": 1.0}
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows, sector_demand=sector))
        self.assertIn("low_base", result.risk_flags)
        self.assertGreater(result.score, 0.0)
        self.assertEqual(result.direction, "positive")

    async def test_no_sector_demand_is_exact_fallback(self):
        """Without sector_demand metadata, the suppressed case scores exactly 0."""
        rows = [
            _row(5, 8, change_pct=300), _row(10, 8), _row(15, 8), _row(60, 2),
        ]
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.direction, "neutral")

    async def test_stale_data_flagged_when_latest_posting_is_old(self):
        """최신 공고가 stale_days(45)보다 오래되면 stale_data + partial.

        stale 판정은 days_since_latest > stale_days 만으로 결정(모멘텀 윈도우와 무관).
        관측 4건(>= min_observations 3)이라 insufficient_history 와는 분리된다.
        """
        rows = [_row(50, 100), _row(55, 100), _row(60, 100), _row(65, 100)]
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertIn("stale_data", result.risk_flags)
        self.assertNotIn("insufficient_history", result.risk_flags)
        self.assertEqual(result.data_status, "partial")

    async def test_ocr_skill_breadth_lifts_score(self):
        """OCR-extracted skill breadth adds a one-sided positive component, so an
        enriched posting set scores strictly higher than the same set unenriched."""
        rows = [_row(5, 100), _row(10, 100), _row(15, 100),
                _row(55, 100), _row(60, 100), _row(65, 100)]  # flat → ~0 own signal
        skilled = [dict(r) for r in rows]
        skilled[0]["ocr_skills"] = ["Python", "Java", "Spring Boot", "Kubernetes", "AWS"]
        skilled[1]["ocr_skills"] = ["Docker", "CI/CD", "React"]  # distinct=8

        baseline = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        enriched = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(skilled))

        self.assertGreater(enriched.score, baseline.score)
        self.assertEqual(enriched.direction, "positive")
        self.assertTrue(any("기술스택" in e.title or "기술스택" in e.summary
                            for e in enriched.evidence_items))

    async def test_no_ocr_skills_is_exact_fallback(self):
        """Without ocr_skills the component contributes 0 — score unchanged."""
        rows = [_row(5, 100), _row(10, 100), _row(15, 100),
                _row(55, 100), _row(60, 100), _row(65, 100)]
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.direction, "neutral")

    async def test_keyword_fusion_surfaces_tech_and_titles(self):
        """Loader-attached tech_stack/job_title are surfaced in summary + a focus
        evidence item, without changing the score (descriptive only)."""
        rows = [
            _row(5, 150, change_pct=20), _row(10, 150, change_pct=20),
            _row(15, 150, change_pct=20),
            _row(55, 100), _row(60, 100), _row(65, 100), _row(70, 100), _row(75, 100),
        ]
        for r in rows[:3]:
            r["tech_stack"] = ["Python", "LLM"]
            r["job_title"] = "백엔드 엔지니어"  # "엔지니어" is a stopword → "백엔드"

        baseline = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(_plain(rows)))
        fused = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))

        # descriptive surfacing present
        self.assertIn("Python", fused.summary)
        self.assertIn("백엔드", fused.summary)
        self.assertTrue(any("포커스" in e.title for e in fused.evidence_items))
        # score is untouched by the keyword fusion
        self.assertEqual(fused.score, baseline.score)
        self.assertEqual(fused.direction, baseline.direction)


def _plain(rows):
    """Copy rows without the descriptive fields (job_title/tech_stack)."""
    keys = ("keyword", "job_category", "job_count", "previous_job_count",
            "change_pct", "observed_date", "seasonal_factor")
    return [{k: r[k] for k in keys} for r in rows]


if __name__ == "__main__":
    unittest.main()
