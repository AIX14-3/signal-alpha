import unittest

from app.analyzers.price.indicators import (
    compute_indicators,
    crossed,
    net_buy_streak,
    rsi,
    sma,
    volatility,
    volume_zscore,
)


def make_rows(closes, volumes=None, foreign=None, institution=None):
    volumes = volumes or [1_000_000] * len(closes)
    foreign = foreign or [None] * len(closes)
    institution = institution or [None] * len(closes)
    return [
        {
            "trade_date": f"2026-01-{index + 1:02d}",
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": volume,
            "foreign_net": foreign_net,
            "institution_net": institution_net,
        }
        for index, (close, volume, foreign_net, institution_net) in enumerate(
            zip(closes, volumes, foreign, institution)
        )
    ]


class SmaTest(unittest.TestCase):
    def test_averages_last_window(self):
        self.assertEqual(sma([1.0, 2.0, 3.0, 4.0], 2), 3.5)

    def test_returns_none_without_enough_history(self):
        self.assertIsNone(sma([1.0, 2.0], 3))


class RsiTest(unittest.TestCase):
    def test_all_gains_is_100(self):
        closes = [float(value) for value in range(100, 116)]
        self.assertEqual(rsi(closes), 100.0)

    def test_balanced_moves_is_50(self):
        closes = [100.0]
        for index in range(14):
            closes.append(closes[-1] + (1.0 if index % 2 == 0 else -1.0))
        self.assertAlmostEqual(rsi(closes), 50.0)

    def test_returns_none_without_enough_history(self):
        self.assertIsNone(rsi([100.0] * 14))


class VolumeZscoreTest(unittest.TestCase):
    def test_detects_spike_against_prior_window(self):
        volumes = [100.0, 200.0] * 10 + [1000.0]
        z = volume_zscore(volumes)
        self.assertIsNotNone(z)
        self.assertGreater(z, 2.0)

    def test_returns_none_for_flat_baseline(self):
        self.assertIsNone(volume_zscore([100.0] * 21))


class VolatilityTest(unittest.TestCase):
    def test_constant_prices_have_zero_volatility(self):
        self.assertEqual(volatility([100.0] * 21), 0.0)

    def test_returns_none_without_enough_history(self):
        self.assertIsNone(volatility([100.0] * 20))


class NetBuyStreakTest(unittest.TestCase):
    def test_counts_consecutive_buying(self):
        self.assertEqual(net_buy_streak([-5, 10, 20, 30]), 3)

    def test_counts_consecutive_selling_as_negative(self):
        self.assertEqual(net_buy_streak([10, -1, -2]), -2)

    def test_none_or_zero_breaks_streak(self):
        self.assertEqual(net_buy_streak([10, None, 20]), 1)
        self.assertEqual(net_buy_streak([10, 0, 20]), 1)
        self.assertEqual(net_buy_streak([]), 0)


class CrossedTest(unittest.TestCase):
    def test_jump_after_flat_history_is_golden_cross(self):
        closes = [100.0] * 24 + [120.0]
        golden, dead = crossed(closes, short=5, long=20)
        self.assertTrue(golden)
        self.assertFalse(dead)

    def test_drop_after_flat_history_is_dead_cross(self):
        closes = [100.0] * 24 + [80.0]
        golden, dead = crossed(closes, short=5, long=20)
        self.assertFalse(golden)
        self.assertTrue(dead)

    def test_flat_series_has_no_cross(self):
        self.assertEqual(crossed([100.0] * 30, short=5, long=20), (False, False))


class ComputeIndicatorsTest(unittest.TestCase):
    def test_populates_all_windows_with_enough_history(self):
        closes = [100.0 + index for index in range(70)]
        rows = make_rows(closes, foreign=[100] * 70, institution=[-50] * 70)

        indicators = compute_indicators(rows)

        self.assertEqual(indicators.sessions, 70)
        self.assertEqual(indicators.last_close, closes[-1])
        self.assertIsNotNone(indicators.sma60)
        self.assertEqual(indicators.rsi14, 100.0)
        self.assertEqual(indicators.foreign_streak, 70)
        self.assertEqual(indicators.institution_streak, -70)
        self.assertEqual(indicators.foreign_net_sum20, 2000)
        self.assertEqual(indicators.institution_net_sum20, -1000)

    def test_short_history_leaves_long_windows_none(self):
        rows = make_rows([100.0] * 10)

        indicators = compute_indicators(rows)

        self.assertEqual(indicators.sessions, 10)
        self.assertIsNone(indicators.sma20)
        self.assertIsNone(indicators.rsi14)
        self.assertIsNone(indicators.volume_z)


if __name__ == "__main__":
    unittest.main()
