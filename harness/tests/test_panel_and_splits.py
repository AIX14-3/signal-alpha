import unittest

import numpy as np
import pandas as pd

from signal_alpha_harness.panel import add_forward_returns
from signal_alpha_harness.splits import (
    FinalSegmentLockedError,
    chronological_split,
    segment_dates,
    walk_forward_windows,
)
from signal_alpha_harness.universe import WATCH_TICKER, load_universe


def make_panel(n_days=100, tickers=("A", "B")):
    dates = pd.bdate_range("2024-01-02", periods=n_days)
    rows = []
    for ticker in tickers:
        for index, day in enumerate(dates):
            close = 100.0 + index
            rows.append(
                {
                    "trade_date": day,
                    "ticker": ticker,
                    "close": close,
                    "volume": 1000,
                    "foreign_net": 1,
                    "institution_net": 1,
                }
            )
    return pd.DataFrame(rows)


class UniverseTest(unittest.TestCase):
    def test_kospi200_snapshot_loads_with_watch_ticker(self):
        try:
            universe = load_universe()
        except FileNotFoundError:
            self.skipTest("universe snapshot not yet generated (snapshot_universe.py)")
        tickers = [stock.ticker for stock in universe]
        self.assertGreaterEqual(len(tickers), 150)  # KOSPI200 — 변경·누락 여유분
        self.assertEqual(len(tickers), len(set(tickers)))
        self.assertIn(WATCH_TICKER, tickers)
        self.assertTrue(all(len(t) == 6 for t in tickers))


class ForwardReturnTest(unittest.TestCase):
    def test_forward_return_uses_future_close_per_ticker(self):
        panel = add_forward_returns(make_panel(), horizons=(5,))
        first_a = panel[(panel["ticker"] == "A")].iloc[0]
        # close goes 100 → 105 five sessions later
        self.assertAlmostEqual(first_a["fwd_ret_5"], 105.0 / 100.0 - 1.0)

    def test_last_rows_have_no_forward_return(self):
        panel = add_forward_returns(make_panel(), horizons=(5,))
        tail = panel[panel["ticker"] == "A"].tail(5)
        self.assertTrue(tail["fwd_ret_5"].isna().all())


class SplitTest(unittest.TestCase):
    def test_60_20_20_split_is_chronological(self):
        panel = make_panel(n_days=100)
        split = chronological_split(panel["trade_date"])
        self.assertEqual(len(split.train_dates), 60)
        self.assertEqual(len(split.valid_dates), 20)
        self.assertEqual(len(split.final_dates), 20)
        self.assertLess(split.train_dates.max(), split.valid_dates.min())
        self.assertLess(split.valid_dates.max(), split.final_dates.min())

    def test_final_segment_is_locked_by_default(self):
        split = chronological_split(make_panel()["trade_date"])
        with self.assertRaises(FinalSegmentLockedError):
            segment_dates(split, "final")
        self.assertEqual(
            len(segment_dates(split, "final", unlock_final=True)), len(split.final_dates)
        )

    def test_walk_forward_windows_do_not_overlap_and_train_precedes_test(self):
        dates = pd.DatetimeIndex(pd.bdate_range("2023-01-02", periods=520))
        windows = walk_forward_windows(dates, test_months=6, min_train_months=12)
        self.assertGreaterEqual(len(windows), 2)
        previous_end = None
        for train, test in windows:
            self.assertLess(train.max(), test.min())
            if previous_end is not None:
                self.assertGreater(test.min(), previous_end)
            previous_end = test.max()


class NoLookaheadTest(unittest.TestCase):
    def test_score_date_only_joins_future_return(self):
        """fwd_ret at T must equal return from T to T+N — never T-N to T."""
        panel = make_panel(n_days=30)
        # make ticker A jump at day 20 only
        jump_day = panel[panel["ticker"] == "A"].iloc[20]["trade_date"]
        panel.loc[
            (panel["ticker"] == "A") & (panel["trade_date"] >= jump_day), "close"
        ] += 50.0
        enriched = add_forward_returns(panel, horizons=(5,))
        before_jump = enriched[
            (enriched["ticker"] == "A")
            & (enriched["trade_date"] < jump_day)
        ].tail(5)
        # the 5 sessions before the jump must already see it in their forward return
        self.assertTrue((before_jump["fwd_ret_5"] > 0.2).any())
        np.testing.assert_array_less(
            0, before_jump["fwd_ret_5"].to_numpy()
        )


if __name__ == "__main__":
    unittest.main()
