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
        # 판정은 여전히 없음(피처 전용) — 이 계약은 불변.
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.data_status, "no_signal")
        self.assertEqual(result.risk_flags, [])
        # 핵심 본질: 행이 있으면 서술 summary 가 생성된다 — 새 자연어 형식("…채용공고 N건…").
        self.assertIn("채용공고", result.summary)
        # 내부용어는 사용자 노출 summary 에서 제거됐다(형식 변경의 목적).
        self.assertNotIn("피처 산출", result.summary)
        self.assertNotIn("메타러너", result.summary)

    async def test_momentum_surfaced_as_percent(self):
        """추세(momentum)가 있으면 자연어 %로 노출된다(최근 150 > 직전 100 → +50%)."""
        rows = [
            _row(5, 150), _row(10, 150), _row(15, 150),
            _row(55, 100), _row(60, 100), _row(65, 100), _row(70, 100), _row(75, 100),
        ]
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertIn("직전 대비", result.summary)
        self.assertIn("%", result.summary)

    async def test_tech_surfaced_but_job_title_noise_excluded(self):
        """tech_stack 은 summary·포커스 evidence 로 노출하되, job_title(스케줄·'모집' 등 노이즈)은 제외한다."""
        rows = [
            _row(5, 150), _row(10, 150), _row(15, 150),
            _row(55, 100), _row(60, 100), _row(65, 100), _row(70, 100), _row(75, 100),
        ]
        for r in rows[:3]:
            r["tech_stack"] = ["Python", "LLM"]
            r["job_title"] = "[주3일, 9시] 물류 모집"  # 스케줄·'모집' 노이즈
        fused = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        # 소재 반영(본질): 기술은 노출 + 포커스 evidence 존재.
        self.assertIn("Python", fused.summary)
        self.assertTrue(any("포커스" in e.title for e in fused.evidence_items))
        # 노이즈 차단(회귀 핵심): job_title 의 '모집'·스케줄 토큰이 summary 에 안 들어간다.
        self.assertNotIn("모집", fused.summary)
        self.assertNotIn("주3일", fused.summary)
        self.assertNotIn("9시", fused.summary)
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
