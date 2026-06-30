"""attention_spike — neutral search-magnitude flag.

Covers the rolling-z (PIT, no look-ahead), tier boundaries, insufficient-history
skip, the multiplier-table empty/populated wording, and the two invariants that
make it safe to ship: it never moves the DataLab direction/score, and the
aggregator routes its note into 주의 근거 (caution) — never 긍정 근거.
"""

import unittest
from dataclasses import replace
from datetime import date, timedelta

from app.aggregator.per_source import build_source_signal
from app.analyzers.config import DataLabRuleConfig
from app.analyzers.datalab import DataLabAnalyzer
from app.analyzers.datalab.attention import compute_attention_spike
from app.schemas.evidence import RawEvidence
from app.schemas.source_result import SourceResult

CONFIG = DataLabRuleConfig()
START = date(2026, 1, 1)

# Prior baseline of 30 points: mean 50, population stdev 10. The 31st point (the
# latest, = as_of) sets the z. value 65 → z (65-50)/10 = 1.5 exactly.
_BASELINE = [40.0] * 15 + [60.0] * 15


def _series(latest_value, *, n_prior=30):
    """[[iso, value], ...] sorted ascending: n_prior baseline points + latest."""
    prior = (_BASELINE * ((n_prior // len(_BASELINE)) + 1))[:n_prior]
    values = prior + [latest_value]
    return [[(START + timedelta(days=i)).isoformat(), v] for i, v in enumerate(values)]


def _as_of(series):
    return date.fromisoformat(series[-1][0])


class ComputeAttentionSpikeTest(unittest.TestCase):
    def test_rolling_z_matches_population_stdev(self):
        series = _series(85.0)  # (85-50)/10 = 3.5
        spike = compute_attention_spike(series, as_of=_as_of(series), config=CONFIG)
        self.assertIsNotNone(spike)
        self.assertAlmostEqual(spike.attention_z, 3.5, places=6)
        self.assertEqual(spike.attention_tier, "급증")
        self.assertEqual(spike.risk_flag, "attention_spike")

    def test_tier_boundaries(self):
        cases = {
            64.0: ("정상", None),  # z 1.4
            65.0: ("주의", "attention_spike"),  # z 1.5
            74.0: ("주의", "attention_spike"),  # z 2.4
            75.0: ("주목", "attention_spike"),  # z 2.5
            84.0: ("주목", "attention_spike"),  # z 3.4
            85.0: ("급증", "attention_spike"),  # z 3.5
        }
        for value, (tier, flag) in cases.items():
            series = _series(value)
            spike = compute_attention_spike(series, as_of=_as_of(series), config=CONFIG)
            self.assertEqual(spike.attention_tier, tier, msg=f"value={value}")
            self.assertEqual(spike.risk_flag, flag, msg=f"value={value}")

    def test_no_lookahead_future_points_dropped(self):
        series = _series(85.0)
        as_of = _as_of(series)
        # A wildly larger FUTURE-dated point must not affect today's z.
        future = series + [[(as_of + timedelta(days=1)).isoformat(), 1000.0]]
        base = compute_attention_spike(series, as_of=as_of, config=CONFIG)
        with_future = compute_attention_spike(future, as_of=as_of, config=CONFIG)
        self.assertAlmostEqual(base.attention_z, with_future.attention_z, places=9)

    def test_insufficient_history_returns_none(self):
        series = _series(85.0, n_prior=20)  # only 20 prior < min_history 30
        self.assertIsNone(
            compute_attention_spike(series, as_of=_as_of(series), config=CONFIG)
        )

    def test_flat_history_is_normal_not_division_error(self):
        series = [[(START + timedelta(days=i)).isoformat(), 50.0] for i in range(31)]
        spike = compute_attention_spike(series, as_of=_as_of(series), config=CONFIG)
        self.assertEqual(spike.attention_z, 0.0)
        self.assertEqual(spike.attention_tier, "정상")

    def test_evidence_text_qualitative_when_multipliers_absent(self):
        # A tier whose multipliers were never calibrated → qualitative wording.
        config = replace(
            CONFIG, attention_vol_mult_surge=None, attention_volume_mult_surge=None
        )
        series = _series(85.0)
        spike = compute_attention_spike(series, as_of=_as_of(series), config=config)
        self.assertIn("향후 거래량·변동성 증가 예상", spike.evidence_text)
        self.assertNotIn("배 예상", spike.evidence_text)
        self.assertIsNone(spike.expected_fwd_volume_mult)

    def test_default_config_is_calibrated_and_cites_numbers(self):
        # Defaults are now calibrated (daily broad-250): 급증 → 거래량 2.3배·변동성 1.4배.
        series = _series(85.0)
        spike = compute_attention_spike(series, as_of=_as_of(series), config=CONFIG)
        self.assertEqual(spike.expected_fwd_volume_mult, 2.29)
        self.assertEqual(spike.expected_fwd_vol_mult, 1.35)
        self.assertIn("2.3배", spike.evidence_text)
        self.assertIn("1.4배", spike.evidence_text)

    def test_evidence_text_cites_numbers_when_calibrated(self):
        config = replace(
            CONFIG, attention_vol_mult_surge=1.71, attention_volume_mult_surge=3.15
        )
        series = _series(85.0)
        spike = compute_attention_spike(series, as_of=_as_of(series), config=config)
        self.assertIn("3.1배", spike.evidence_text)
        self.assertIn("1.7배", spike.evidence_text)
        self.assertEqual(spike.expected_fwd_volume_mult, 3.15)


def _row(days_ago, search_index):
    return {
        "category_id": 1,
        "weight": 1.0,
        "keyword": "k",
        "keyword_group": "g",
        "observed_date": (date(2026, 6, 11) - timedelta(days=days_ago)).isoformat(),
        "search_index": search_index,
        "previous_search_index": None,
        "change_pct": None,
        "is_spike": False,
        "polarity": "demand",
        "polarity_source": "default",
        "polarity_model": None,
    }


_RISING_ROWS = [_row(2, 70), _row(4, 70), _row(18, 50), _row(20, 50), _row(24, 50)]


class AnalyzerAttentionTest(unittest.IsolatedAsyncioTestCase):
    async def test_attention_attached_without_changing_verdict(self):
        series = _series(85.0)  # 급증
        evidence = [
            RawEvidence(
                source="DATALAB",
                stock_code="005930",
                title="t",
                content="",
                metadata={
                    "rows": _RISING_ROWS,
                    "as_of": series[-1][0],
                    "attention_series": series,
                },
            )
        ]
        result = await DataLabAnalyzer(CONFIG).analyze("005930", evidence)
        # Invariant: attention is neutral — verdict stays unknown/0/no_signal.
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.data_status, "no_signal")
        # ...but the neutral spike is surfaced as a structured field + flag + note.
        self.assertEqual(result.attention_tier, "급증")
        self.assertIn("attention_spike", result.risk_flags)
        self.assertIsNotNone(result.attention_note)

    async def test_no_attention_series_leaves_output_unchanged(self):
        # The existing feature-only path (no attention_series) must be untouched.
        evidence = [
            RawEvidence(
                source="DATALAB",
                stock_code="005930",
                title="t",
                content="",
                metadata={"rows": _RISING_ROWS, "as_of": "2026-06-11"},
            )
        ]
        result = await DataLabAnalyzer(CONFIG).analyze("005930", evidence)
        self.assertEqual(result.risk_flags, [])
        self.assertIsNone(result.attention_tier)
        self.assertIsNone(result.attention_note)


class AggregatorAttentionRoutingTest(unittest.TestCase):
    def test_attention_note_routes_to_caution_not_positive(self):
        result = SourceResult(
            source="DATALAB",
            stock_code="005930",
            direction="unknown",
            score=0.0,
            summary="검색 트렌드 피처 산출",
            data_status="no_signal",
            risk_flags=["attention_spike"],
            attention_tier="급증",
            attention_z=3.5,
            attention_note="검색 급증(z 3.5, 급증 등급) — 향후 거래량·변동성 증가 예상. 방향 아님, 주의 신호.",
        )
        signal = build_source_signal(result)
        self.assertEqual(signal.score, 0.0)
        self.assertEqual(signal.positive_evidence, [])
        self.assertTrue(any("향후 거래량" in c for c in signal.caution_evidence))


if __name__ == "__main__":
    unittest.main()
