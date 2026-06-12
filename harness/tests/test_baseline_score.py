import unittest

import pandas as pd

from signal_alpha_harness.baseline_score import add_baseline_score


def make_panel(closes, foreign=None):
    n = len(closes)
    return pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2024-01-02", periods=n),
            "ticker": ["A"] * n,
            "close": closes,
            "volume": [1000] * n,
            "foreign_net": foreign or [0] * n,
            "institution_net": [0] * n,
        }
    )


class BaselineScoreTest(unittest.TestCase):
    def test_uptrend_with_foreign_buying_scores_positive(self):
        closes = [100.0 + i for i in range(40)]
        panel = make_panel(closes, foreign=[100] * 40)
        scored = add_baseline_score(panel)
        self.assertGreater(scored["score"].iloc[-1], 0.3)

    def test_downtrend_scores_negative(self):
        closes = [140.0 - i for i in range(40)]
        scored = add_baseline_score(make_panel(closes))
        self.assertLess(scored["score"].iloc[-1], -0.3)

    def test_insufficient_history_is_nan(self):
        scored = add_baseline_score(make_panel([100.0] * 10))
        self.assertTrue(scored["score"].isna().all())

    def test_score_is_clamped(self):
        closes = [100.0 * (1.1 ** i) for i in range(40)]
        scored = add_baseline_score(make_panel(closes, foreign=[100] * 40))
        self.assertLessEqual(scored["score"].max(), 1.0)
        self.assertGreaterEqual(scored["score"].min(), -1.0)


if __name__ == "__main__":
    unittest.main()
