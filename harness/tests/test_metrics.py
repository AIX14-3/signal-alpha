import unittest

import numpy as np
import pandas as pd

from signal_alpha_harness.metrics import (
    compute_metrics,
    direction_hit_rate,
    permutation_pvalue,
)


def make_frame(n_days=120, n_tickers=20, signal_strength=1.0, seed=7):
    """Synthetic panel where score predicts fwd_ret_5 with given strength."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n_days)
    rows = []
    for day in dates:
        scores = rng.uniform(-1, 1, n_tickers)
        noise = rng.normal(0, 0.02, n_tickers)
        returns = signal_strength * 0.02 * scores + noise
        for index in range(n_tickers):
            rows.append(
                {
                    "trade_date": day,
                    "ticker": f"T{index:02d}",
                    "score": scores[index],
                    "fwd_ret_5": returns[index],
                }
            )
    return pd.DataFrame(rows)


class DirectionHitRateTest(unittest.TestCase):
    def test_counts_only_directional_calls(self):
        frame = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2024-01-02"] * 4),
                "ticker": ["A", "B", "C", "D"],
                "score": [0.5, -0.5, 0.1, 0.5],
                "fwd_ret_5": [0.03, -0.02, 0.10, -0.01],
            }
        )
        hit, n_directional = direction_hit_rate(frame, 5)
        # A: positive call & up → hit, B: negative & down → hit, C: neutral 제외, D: positive & down → miss
        self.assertEqual(n_directional, 3)
        self.assertAlmostEqual(hit, 2 / 3)

    def test_returns_none_when_all_neutral(self):
        frame = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2024-01-02"]),
                "ticker": ["A"],
                "score": [0.0],
                "fwd_ret_5": [0.01],
            }
        )
        hit, n_directional = direction_hit_rate(frame, 5)
        self.assertIsNone(hit)
        self.assertEqual(n_directional, 0)


class ComputeMetricsTest(unittest.TestCase):
    def test_planted_signal_yields_positive_ic_and_spread(self):
        report = compute_metrics(make_frame(signal_strength=1.0), 5)
        self.assertGreater(report.mean_ic, 0.2)
        self.assertGreater(report.ic_positive_share, 0.7)
        self.assertGreater(report.quantile_spread, 0)
        self.assertGreater(report.hit_rate, 0.55)

    def test_no_signal_yields_near_zero_ic(self):
        report = compute_metrics(make_frame(signal_strength=0.0), 5)
        self.assertLess(abs(report.mean_ic), 0.05)


class PermutationTest(unittest.TestCase):
    def test_planted_signal_passes_gate(self):
        p = permutation_pvalue(make_frame(n_days=60, signal_strength=1.0), 5, n_permutations=100)
        self.assertLess(p, 0.05)

    def test_no_signal_fails_gate(self):
        # 시드별 p값은 균등분포를 따르므로, 여러 시드의 중앙값으로 안정적으로 검증한다
        pvalues = [
            permutation_pvalue(
                make_frame(n_days=60, signal_strength=0.0, seed=seed), 5, n_permutations=100
            )
            for seed in (3, 7, 11)
        ]
        self.assertGreater(sorted(pvalues)[1], 0.05)


if __name__ == "__main__":
    unittest.main()
