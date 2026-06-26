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
    """Phase 0 (#525): 결정론 판정 제거 — 피처 산출만. 방향/점수 verdict 없음."""

    async def test_no_data_is_no_signal(self):
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence([]))
        self.assertEqual(result.data_status, "no_signal")
        self.assertIn("no_data", result.risk_flags)

    async def test_rows_present_is_feature_only_no_verdict(self):
        rows = [
            _row(5, 150, change_pct=20), _row(10, 150, change_pct=20), _row(15, 150, change_pct=20),
            _row(55, 100), _row(60, 100), _row(65, 100), _row(70, 100), _row(75, 100),
        ]
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.data_status, "no_signal")
        self.assertEqual(result.risk_flags, [])
        self.assertIn("피처 산출", result.summary)

    async def test_keyword_fusion_surfaces_tech_and_titles(self):
        """Loader-attached tech_stack/job_title 는 판정과 무관하게 summary + 포커스 evidence 로 노출."""
        rows = [
            _row(5, 150, change_pct=20), _row(10, 150, change_pct=20), _row(15, 150, change_pct=20),
            _row(55, 100), _row(60, 100), _row(65, 100), _row(70, 100), _row(75, 100),
        ]
        for r in rows[:3]:
            r["tech_stack"] = ["Python", "LLM"]
            r["job_title"] = "백엔드 엔지니어"  # "엔지니어" is a stopword -> "백엔드"
        fused = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertIn("Python", fused.summary)
        self.assertIn("백엔드", fused.summary)
        self.assertTrue(any("포커스" in e.title for e in fused.evidence_items))
        # 판정은 여전히 없음(피처 전용).
        self.assertEqual(fused.direction, "unknown")
        self.assertEqual(fused.score, 0.0)


def _plain(rows):
    """Copy rows without the descriptive fields (job_title/tech_stack)."""
    keys = ("keyword", "job_category", "job_count", "previous_job_count",
            "change_pct", "observed_date", "seasonal_factor")
    return [{k: r[k] for k in keys} for r in rows]


if __name__ == "__main__":
    unittest.main()
