"""Phase 3 단위 검증 — 결합/보정표/확신도/ScoreCard."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from signal_core.quant.calibration import build_calibration, lookup, monotonicity
from signal_alpha_harness.combine import MIN_FACTORS_FOR_SCORE, _winsorize_zscore, add_combined_score
from signal_core.quant.confidence import add_confidence, grade_row
from signal_core.quant.scorecard import build_scorecard


class WinsorizeZscoreTest(unittest.TestCase):
    def test_outlier_is_clipped_before_zscore(self):
        values = pd.Series([1.0, 2.0, 3.0, 4.0, 1000.0])
        z = _winsorize_zscore(values)
        self.assertLess(abs(float(z.iloc[-1])), 3.5)  # 클립 없이면 ~2 아닌 극단값

    def test_zero_std_returns_nan(self):
        z = _winsorize_zscore(pd.Series([5.0, 5.0, 5.0]))
        self.assertTrue(z.isna().all())


class CombinedScoreTest(unittest.TestCase):
    def make_panel(self, n_days=400, tickers=("A", "B", "C", "D", "E")):
        dates = pd.bdate_range("2023-01-02", periods=n_days)
        rows = []
        rng = np.random.default_rng(7)
        for i, date in enumerate(dates):
            for j, ticker in enumerate(tickers):
                drift = 0.0005 * (j - 2)
                rows.append(
                    {
                        "trade_date": date,
                        "ticker": ticker,
                        "close": 100.0 * float(np.exp(drift * i + 0.01 * rng.standard_normal())),
                        "volume": 1000,
                    }
                )
        return pd.DataFrame(rows)

    def test_score_is_percentile_0_100(self):
        scored = add_combined_score(self.make_panel(), None)
        valid = scored["score"].dropna()
        self.assertGreater(len(valid), 0)
        self.assertGreaterEqual(valid.min(), 0.0)
        self.assertLessEqual(valid.max(), 100.0)

    def test_score_withheld_when_too_few_factors(self):
        scored = add_combined_score(self.make_panel(), None)
        thin = scored["n_factors_used"] < MIN_FACTORS_FOR_SCORE
        self.assertTrue(scored.loc[thin, "score"].isna().all())


class CalibrationTest(unittest.TestCase):
    def make_scored(self):
        # 점수와 미래수익이 정확히 비례하는 합성 프레임 → 단조 보정표
        rng = np.random.default_rng(11)
        dates = pd.bdate_range("2024-01-02", periods=60)
        rows = []
        for date in dates:
            for k in range(20):
                score = k * 5 + 2.5
                rows.append(
                    {
                        "trade_date": date,
                        "ticker": f"T{k}",
                        "score": score,
                        "fwd_ret_20": score / 1000.0 + rng.normal(0, 0.001),
                    }
                )
        return pd.DataFrame(rows)

    def test_monotone_table_passes_gate(self):
        table = build_calibration(self.make_scored(), horizon=20)
        self.assertEqual(len(table), 10)
        mono = monotonicity(table)
        self.assertTrue(mono["passed"])

    def test_lookup_returns_bucket_distribution(self):
        table = build_calibration(self.make_scored(), horizon=20)
        info = lookup(table, 78.0)
        self.assertEqual(info["horizon_days"], 20)
        self.assertEqual(len(info["p25_p75"]), 2)
        self.assertGreater(info["sample_size"], 0)

    def test_excess_is_relative_to_market(self):
        # 모든 종목 수익이 동일하면 초과수익 분포는 전 버킷 0
        frame = self.make_scored()
        frame["fwd_ret_20"] = 0.05
        table = build_calibration(frame, horizon=20)
        self.assertTrue((table["median_excess"].abs() < 1e-12).all())


class ConfidenceTest(unittest.TestCase):
    def test_grade_rules(self):
        self.assertEqual(grade_row(1, 0.5, 3), "C")  # 팩터 부족
        self.assertEqual(grade_row(3, 0.99, 3), "C")  # 변동성 극단
        self.assertEqual(grade_row(2, 0.5, 3), "B")  # 팩터 일부 결측
        self.assertEqual(grade_row(3, 0.9, 3), "B")  # 변동성 상위
        self.assertEqual(grade_row(3, 0.5, 3), "A")

    def test_add_confidence_emits_grades(self):
        panel = CombinedScoreTest().make_panel()
        scored = add_combined_score(panel, None)
        graded = add_confidence(scored, total_factors=3)
        self.assertTrue(set(graded["confidence"].unique()) <= {"A", "B", "C"})


class ScoreCardTest(unittest.TestCase):
    def test_c_grade_withholds_score(self):
        table = build_calibration(CalibrationTest().make_scored(), horizon=20)
        row = pd.Series(
            {"ticker": "005930", "score": 78.0, "confidence": "C",
             "z_reversal_1m": 0.5, "z_lowvol_60": np.nan, "z_quality_margin_yoy": 0.1}
        )
        card = build_scorecard(row, table)
        self.assertIsNone(card.score)
        self.assertIsNone(card.calibration)
        self.assertIn("저변동성 결측", card.drivers)
        self.assertTrue(card.warning and card.disclaimer)

    def test_a_grade_full_card(self):
        table = build_calibration(CalibrationTest().make_scored(), horizon=20)
        row = pd.Series(
            {"ticker": "000660", "score": 78.0, "confidence": "A",
             "z_reversal_1m": 0.5, "z_lowvol_60": -0.4, "z_quality_margin_yoy": 0.0}
        )
        card = build_scorecard(row, table)
        self.assertEqual(card.score, 78)
        self.assertEqual(card.calibration["horizon_days"], 20)
        self.assertEqual(card.drivers, ["단기반전 +", "저변동성 -", "마진개선 중립"])


if __name__ == "__main__":
    unittest.main()
