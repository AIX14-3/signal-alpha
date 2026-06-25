import unittest
from datetime import date, timedelta

from app.analyzers.config import DataLabRuleConfig
from app.analyzers.datalab import DataLabAnalyzer
from app.schemas.evidence import RawEvidence

AS_OF = date(2026, 6, 11)
CONFIG = DataLabRuleConfig(
    lookback_days=30,
    min_observations=5,
    momentum_threshold=0.1,
    spike_threshold=0.2,
    stale_days=14,
    momentum_weight=0.6,
    spike_weight=0.2,
    change_weight=0.2,
    positive_threshold=0.2,
    negative_threshold=-0.2,
)


def _row(
    days_ago,
    search_index,
    *,
    is_spike=False,
    change_pct=None,
    weight=1.0,
    polarity="demand",
    polarity_source="default",
    polarity_model=None,
):
    return {
        "category_id": 1,
        "weight": weight,
        "keyword": "k",
        "keyword_group": "g",
        "observed_date": (AS_OF - timedelta(days=days_ago)).isoformat(),
        "search_index": search_index,
        "previous_search_index": None,
        "change_pct": change_pct,
        "is_spike": is_spike,
        "polarity": polarity,
        "polarity_source": polarity_source,
        "polarity_model": polarity_model,
    }


def _evidence(rows):
    return [
        RawEvidence(
            source="DATALAB",
            stock_code="005930",
            title="t",
            content="",
            metadata={"rows": rows, "as_of": AS_OF.isoformat(), "lookback_days": 30},
        )
    ]


class DataLabAnalyzerTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_data_is_no_signal(self):
        # Ran but had no rows → "no_signal" (the report shows "시그널이 없습니다"),
        # distinct from "failed" (a loader/analyzer error) and "missing" (never ran).
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence([]))
        self.assertEqual(result.data_status, "no_signal")
        self.assertIn("no_data", result.risk_flags)

    async def test_rising_search_index_is_positive(self):
        # prior window needs >= min_prior_observations (5) to avoid the guard.
        rows = [
            _row(2, 70), _row(4, 70), _row(6, 70),
            _row(18, 50), _row(20, 50), _row(22, 50), _row(24, 50), _row(26, 50),
        ]
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "positive")
        # graded (tanh) scoring: +40% momentum → ~0.52, not the old fixed 0.6.
        self.assertGreater(result.score, 0.4)
        self.assertLess(result.score, 0.6)
        self.assertNotIn("low_base", result.risk_flags)

    async def test_falling_index_with_spikes_is_mixed(self):
        rows = [
            _row(2, 40, is_spike=True),
            _row(4, 40, is_spike=True),
            _row(6, 40),
            _row(18, 80), _row(20, 80), _row(22, 80), _row(24, 80), _row(26, 80),
        ]
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "mixed")
        self.assertIn("search_spike", result.risk_flags)

    async def test_too_few_observations_flag_partial(self):
        rows = [_row(2, 70), _row(20, 50)]
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertIn("insufficient_history", result.risk_flags)
        self.assertEqual(result.data_status, "partial")

    async def test_low_base_guard_suppresses_momentum(self):
        """Tiny prior window → ratio-based momentum/change suppressed."""
        rows = [_row(2, 90), _row(4, 90), _row(6, 90), _row(20, 10)]  # 1 prior obs
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertIn("low_base", result.risk_flags)
        # momentum suppressed → no positive momentum score
        self.assertEqual(result.score, 0.0)

    async def test_rising_risk_keywords_is_negative(self):
        """Rising RISK-keyword searches (리콜/불매…) flip the signal negative."""
        rows = [
            _row(2, 80, polarity="risk"), _row(4, 80, polarity="risk"), _row(6, 80, polarity="risk"),
            _row(18, 40, polarity="risk"), _row(20, 40, polarity="risk"), _row(22, 40, polarity="risk"),
            _row(24, 40, polarity="risk"), _row(26, 40, polarity="risk"),
        ]
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "negative")
        self.assertLess(result.score, 0.0)
        self.assertIn("risk_search", result.risk_flags)

    async def test_llm_polarity_sets_provenance(self):
        """LLM-classified polarity rows surface llm_model + summary disclosure."""
        rows = [
            _row(2, 70, polarity_source="llm", polarity_model="gemini-2.5-flash-lite"),
            _row(4, 70, polarity_source="llm", polarity_model="gemini-2.5-flash-lite"),
            _row(6, 70),  # one manual/default row mixed in
            _row(18, 50), _row(20, 50), _row(22, 50), _row(24, 50), _row(26, 50),
        ]
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.llm_model, "gemini-2.5-flash-lite")
        self.assertIn("LLM 분류 키워드 2건", result.summary)

    async def test_provenance_does_not_change_score(self):
        """Scope A guarantee: provenance is read-only — same rows with/without LLM
        tagging produce an identical score and direction."""
        base = [
            _row(2, 70), _row(4, 70), _row(6, 70),
            _row(18, 50), _row(20, 50), _row(22, 50), _row(24, 50), _row(26, 50),
        ]
        tagged = [
            _row(2, 70, polarity_source="llm", polarity_model="m"),
            _row(4, 70, polarity_source="llm", polarity_model="m"),
            _row(6, 70, polarity_source="llm", polarity_model="m"),
            _row(18, 50), _row(20, 50), _row(22, 50), _row(24, 50), _row(26, 50),
        ]
        a = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(base))
        b = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(tagged))
        self.assertEqual(a.score, b.score)
        self.assertEqual(a.direction, b.direction)
        self.assertIsNone(a.llm_model)
        self.assertEqual(b.llm_model, "m")

    async def test_stale_data_flagged_when_latest_observation_is_old(self):
        """최신 관측이 stale_days(14)보다 오래되면 stale_data + partial.

        stale 판정은 days_since_latest > stale_days 만으로 결정(모멘텀 윈도우와 무관).
        관측 6건(>= min_observations 5)이라 insufficient_history 와는 분리된다.
        """
        rows = [_row(16, 60), _row(18, 60), _row(20, 60), _row(22, 60), _row(24, 60), _row(26, 60)]
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertIn("stale_data", result.risk_flags)
        self.assertNotIn("insufficient_history", result.risk_flags)
        self.assertEqual(result.data_status, "partial")

    async def test_demand_outweighs_mild_risk(self):
        """Strong demand momentum + only mild risk → still positive (demand − risk)."""
        rows = [
            _row(2, 70), _row(4, 70), _row(6, 70),
            _row(18, 50), _row(20, 50), _row(22, 50), _row(24, 50), _row(26, 50),
            # mild risk (no real prior-window risk data → risk component 0)
            _row(3, 30, polarity="risk"),
        ]
        result = await DataLabAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "positive")


if __name__ == "__main__":
    unittest.main()
