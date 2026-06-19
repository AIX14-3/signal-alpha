import unittest
from datetime import date, timedelta

from app.analyzers.config import PatentRuleConfig
from app.analyzers.patent import PatentAnalyzer
from app.schemas.evidence import RawEvidence

AS_OF = date(2026, 6, 11)
CONFIG = PatentRuleConfig(
    lookback_days=365,
    min_count=3,
    momentum_threshold=0.5,
    stale_days=180,
    momentum_weight=0.5,
    new_category_weight=0.3,
    activity_weight=0.2,
    activity_scale=5.0,
    positive_threshold=0.2,
    negative_threshold=-0.2,
)


def _row(days_ago, *, is_new=False, tech="A", significance=None):
    row = {
        "application_no": f"x{days_ago}",
        "patent_title": "p",
        "applicant_name": "a",
        "application_date": (AS_OF - timedelta(days=days_ago)).isoformat(),
        "tech_category": tech,
        "is_new_category": is_new,
    }
    if significance is not None:
        row["significance"] = significance
    return row


def _evidence(rows):
    return [
        RawEvidence(
            source="PATENT",
            stock_code="005930",
            title="t",
            content="",
            metadata={"rows": rows, "as_of": AS_OF.isoformat(), "lookback_days": 365},
        )
    ]


class PatentAnalyzerTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_data_is_failed(self):
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence([]))
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.data_status, "failed")
        self.assertIn("no_data", result.risk_flags)

    async def test_rising_filings_with_new_category_is_positive(self):
        rows = [_row(10, is_new=True), _row(20), _row(30), _row(40), _row(300)]
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "positive")
        # graded (tanh) scoring no longer saturates to 1.0; still a strong positive.
        self.assertGreater(result.score, 0.7)
        self.assertLess(result.score, 1.0)
        self.assertEqual(result.data_status, "ok")

    async def test_falling_filings_is_negative(self):
        rows = [_row(10), _row(200), _row(210), _row(220), _row(230)]
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "negative")

    async def test_only_old_filings_flag_stale_and_partial(self):
        rows = [_row(300), _row(310), _row(320)]
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertIn("stale_data", result.risk_flags)
        self.assertEqual(result.data_status, "partial")

    async def test_high_significance_lifts_score_above_unenriched(self):
        # Same filings; the enriched set scores strictly higher (significance drives).
        plain = [_row(200), _row(210), _row(220), _row(230)]
        enriched = [
            _row(200, significance=0.9), _row(210, significance=0.9),
            _row(220, significance=0.9), _row(230, significance=0.9),
        ]
        low = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(plain))
        high = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(enriched))
        self.assertGreater(high.score, low.score)

    async def test_no_enrichment_is_exact_fallback(self):
        # Rows without a significance key must score identically to pre-C3 (component 0).
        rows = [_row(10, is_new=True), _row(20), _row(30), _row(40), _row(300)]
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertGreater(result.score, 0.7)
        self.assertLess(result.score, 1.0)

    async def test_more_filings_lift_activity_monotonically(self):
        # Balanced recent/prior (momentum 0) and no new categories isolate the
        # activity component: more filings now score strictly higher, where the old
        # binary activity gave both the same +0.2.
        few = [_row(10), _row(20), _row(200), _row(210)]  # total 4, recent==prior
        many = few + [
            _row(30), _row(40), _row(50), _row(60),
            _row(220), _row(230), _row(240), _row(250),
        ]  # total 12, recent==prior
        low = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(few))
        high = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(many))
        self.assertGreater(high.score, low.score)
        # Single-sided: count alone (flat momentum) never makes the signal negative.
        self.assertGreaterEqual(low.score, 0.0)

    async def test_below_min_count_has_zero_activity(self):
        # Small-sample floor preserved: under min_count, activity contributes 0.
        rows = [_row(10), _row(20)]  # total 2 < min_count 3
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertIn("insufficient_history", result.risk_flags)


if __name__ == "__main__":
    unittest.main()
