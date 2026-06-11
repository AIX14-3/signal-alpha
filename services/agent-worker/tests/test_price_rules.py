import unittest

from app.analyzers.price.indicators import PriceIndicators
from app.analyzers.price.rules import evaluate_indicators


def make_indicators(**overrides):
    defaults = dict(
        sessions=120,
        last_close=100.0,
        sma5=100.0,
        sma20=100.0,
        sma60=100.0,
        golden_cross=False,
        dead_cross=False,
        rsi14=None,
        volume_z=0.0,
        volatility20=1.0,
        foreign_streak=0,
        institution_streak=0,
        foreign_net_sum20=None,
        institution_net_sum20=None,
    )
    defaults.update(overrides)
    return PriceIndicators(**defaults)


class EvaluateIndicatorsTest(unittest.TestCase):
    def test_insufficient_history_is_unknown(self):
        assessment = evaluate_indicators(make_indicators(sessions=10))

        self.assertEqual(assessment.direction, "unknown")
        self.assertEqual(assessment.score, 0.0)
        self.assertIn("insufficient_history", assessment.risk_flags)

    def test_bullish_alignment_and_flows_score_positive(self):
        assessment = evaluate_indicators(
            make_indicators(
                sma5=110.0,
                sma20=105.0,
                sma60=100.0,
                golden_cross=True,
                rsi14=60.0,
                foreign_streak=4,
                institution_streak=3,
                foreign_net_sum20=5000,
                institution_net_sum20=3000,
            )
        )

        self.assertEqual(assessment.direction, "positive")
        self.assertAlmostEqual(assessment.score, 0.9)
        self.assertTrue(any("정배열" in highlight for highlight in assessment.highlights))

    def test_bearish_alignment_and_flows_score_negative(self):
        assessment = evaluate_indicators(
            make_indicators(
                sma5=90.0,
                sma20=95.0,
                sma60=100.0,
                dead_cross=True,
                rsi14=40.0,
                foreign_streak=-4,
                institution_streak=-3,
                foreign_net_sum20=-5000,
                institution_net_sum20=-3000,
            )
        )

        self.assertEqual(assessment.direction, "negative")
        self.assertAlmostEqual(assessment.score, -0.9)

    def test_conflicting_trend_and_flow_is_mixed(self):
        assessment = evaluate_indicators(
            make_indicators(
                sma5=110.0,
                sma20=105.0,
                sma60=100.0,
                foreign_streak=-4,
                institution_streak=-3,
                foreign_net_sum20=-5000,
                institution_net_sum20=-3000,
            )
        )

        self.assertEqual(assessment.direction, "mixed")

    def test_small_signals_stay_neutral(self):
        assessment = evaluate_indicators(make_indicators(rsi14=55.0))

        self.assertEqual(assessment.direction, "neutral")
        self.assertAlmostEqual(assessment.score, 0.1)

    def test_overbought_rsi_flags_and_penalizes(self):
        assessment = evaluate_indicators(make_indicators(rsi14=75.0))

        self.assertIn("overbought", assessment.risk_flags)
        self.assertAlmostEqual(assessment.score, -0.1)

    def test_oversold_rsi_flags_without_score(self):
        assessment = evaluate_indicators(make_indicators(rsi14=25.0))

        self.assertIn("oversold", assessment.risk_flags)
        self.assertAlmostEqual(assessment.score, 0.0)

    def test_volatility_and_volume_flags(self):
        assessment = evaluate_indicators(make_indicators(volatility20=4.5, volume_z=2.5))

        self.assertIn("high_volatility", assessment.risk_flags)
        self.assertIn("volume_spike", assessment.risk_flags)

    def test_short_history_is_flagged_but_scored(self):
        assessment = evaluate_indicators(make_indicators(sessions=30, rsi14=55.0))

        self.assertIn("short_history", assessment.risk_flags)
        self.assertEqual(assessment.direction, "neutral")

    def test_score_is_clamped_to_unit_range(self):
        assessment = evaluate_indicators(
            make_indicators(
                sma5=110.0,
                sma20=105.0,
                sma60=100.0,
                golden_cross=True,
                rsi14=60.0,
                foreign_streak=10,
                institution_streak=10,
                foreign_net_sum20=1,
                institution_net_sum20=1,
            )
        )

        self.assertLessEqual(assessment.score, 1.0)


if __name__ == "__main__":
    unittest.main()
