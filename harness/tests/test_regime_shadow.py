"""Phase 4-5 단위 검증 — 국면 라벨(PIT)과 섀도 기록(append-only·중복 거부)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from signal_alpha_harness.regime import label_regimes, regime_ic_breakdown
from signal_alpha_harness.shadow import append_predictions, evaluate_records, recorded_trade_dates


def make_market_panel(daily_return: float, n_days: int = 120, tickers=("A", "B", "C")):
    dates = pd.bdate_range("2024-01-02", periods=n_days)
    rows = []
    for i, date in enumerate(dates):
        for ticker in tickers:
            rows.append(
                {
                    "trade_date": date,
                    "ticker": ticker,
                    "close": 100.0 * (1 + daily_return) ** i,
                    "volume": 1000,
                }
            )
    return pd.DataFrame(rows)


class RegimeLabelTest(unittest.TestCase):
    def test_uptrend_labels_bull(self):
        labels = label_regimes(make_market_panel(+0.002))  # 60일 누적 ~ +13%
        self.assertEqual(labels.dropna().unique().tolist(), ["bull"])

    def test_downtrend_labels_bear(self):
        labels = label_regimes(make_market_panel(-0.002))
        self.assertEqual(labels.dropna().unique().tolist(), ["bear"])

    def test_sideways_labels_flat(self):
        labels = label_regimes(make_market_panel(0.0))
        self.assertEqual(labels.dropna().unique().tolist(), ["flat"])

    def test_label_is_point_in_time(self):
        """T일 라벨은 미래 가격 변조에 불변해야 한다."""
        panel = make_market_panel(+0.002)
        cutoff = panel["trade_date"].sort_values().unique()[90]
        full = label_regimes(panel)
        mutated = panel.copy()
        mutated.loc[mutated["trade_date"] > cutoff, "close"] *= 0.5
        partial = label_regimes(mutated)
        pd.testing.assert_series_equal(
            full[full.index <= cutoff], partial[partial.index <= cutoff]
        )

    def test_breakdown_reports_three_regimes(self):
        panel = make_market_panel(+0.002)
        panel["score"] = np.tile([1.0, 2.0, 3.0], len(panel) // 3)
        panel["fwd_ret_20"] = 0.01
        table = regime_ic_breakdown(panel, horizon=20)
        self.assertEqual(list(table["regime"]), ["bull", "flat", "bear"])


class ShadowAppendTest(unittest.TestCase):
    def make_day_rows(self, trade_date="2026-06-12"):
        return pd.DataFrame(
            {
                "trade_date": [pd.Timestamp(trade_date)] * 2,
                "ticker": ["005930", "000660"],
                "score": [78.0, np.nan],
                "confidence": ["A", "C"],
                "n_factors_used": [3, 1],
            }
        )

    def test_append_then_duplicate_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "predictions.jsonl"
            first = append_predictions(path, self.make_day_rows())
            self.assertEqual(first, 2)
            second = append_predictions(path, self.make_day_rows())
            self.assertEqual(second, 0)  # 같은 거래일 재기록 거부
            self.assertEqual(recorded_trade_dates(path), {"2026-06-12"})
            lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(lines), 2)
            self.assertIsNone(lines[1]["score"])  # C등급 보류는 null로 기록
            self.assertIn("predicted_at", lines[0])

    def test_evaluate_joins_realized_returns(self):
        # 점수 순서 = 실현 수익 순서인 합성 데이터 → 섀도 IC=1
        predictions = pd.DataFrame(
            {
                "trade_date": ["2024-01-02"] * 5,
                "ticker": [f"T{k}" for k in range(5)],
                "score": [10.0, 30.0, 50.0, 70.0, 90.0],
            }
        )
        dates = pd.bdate_range("2024-01-02", periods=25)
        rows = []
        for k in range(5):
            for i, date in enumerate(dates):
                growth = 1.0 + 0.001 * k  # 점수 높은 종목이 더 오름
                rows.append({"trade_date": date, "ticker": f"T{k}", "close": 100.0 * growth**i})
        closes = pd.DataFrame(rows)
        report = evaluate_records(predictions, closes, horizon=20)
        self.assertEqual(len(report), 1)
        self.assertAlmostEqual(float(report["shadow_ic"].iloc[0]), 1.0)

    def test_evaluate_skips_immature_predictions(self):
        predictions = pd.DataFrame(
            {"trade_date": ["2024-01-02"] * 5, "ticker": [f"T{k}" for k in range(5)],
             "score": [10.0, 30.0, 50.0, 70.0, 90.0]}
        )
        dates = pd.bdate_range("2024-01-02", periods=10)  # horizon 20 미경과
        closes = pd.DataFrame(
            [{"trade_date": d, "ticker": f"T{k}", "close": 100.0} for d in dates for k in range(5)]
        )
        report = evaluate_records(predictions, closes, horizon=20)
        self.assertTrue(report.empty)


if __name__ == "__main__":
    unittest.main()
