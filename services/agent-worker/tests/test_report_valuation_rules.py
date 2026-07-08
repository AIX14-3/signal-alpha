"""REPORT 결정론 밸류에이션 점수(목표주가 리비전 + upside) 단위 테스트.

방향은 매수 편향된 투자의견이 아니라 목표주가에서 나온다. 리비전(최신 vs 직전 목표가)이 주 신호,
upside(목표가 vs 현재가)는 편향 완충을 위해 낮은 가중·큰 scale.
"""

import unittest

from app.analyzers.config import ReportRuleConfig
from app.analyzers.report.rules import (
    compute_revision_pct,
    compute_upside_pct,
    evaluate_report,
)

CFG = ReportRuleConfig()


class ComputeInputsTest(unittest.TestCase):
    def test_revision_pct(self):
        self.assertAlmostEqual(
            compute_revision_pct(9000.0, 8000.0, min_target_price=1.0), 0.125
        )

    def test_revision_none_when_missing_prior(self):
        self.assertIsNone(compute_revision_pct(9000.0, None, min_target_price=1.0))

    def test_revision_none_when_prior_below_floor(self):
        self.assertIsNone(compute_revision_pct(9000.0, 0.0, min_target_price=1.0))

    def test_upside_pct(self):
        self.assertAlmostEqual(
            compute_upside_pct(9000.0, 8000.0, min_price=1.0), 0.125
        )

    def test_upside_none_when_missing_price(self):
        self.assertIsNone(compute_upside_pct(9000.0, None, min_price=1.0))


class EvaluateReportTest(unittest.TestCase):
    def test_positive_revision_scores_positive(self):
        a = evaluate_report(revision_pct=0.10, upside_pct=None, config=CFG)
        self.assertTrue(a.has_signal)
        self.assertGreater(a.score, 0.2)
        self.assertEqual(a.direction, "positive")

    def test_negative_revision_scores_negative(self):
        a = evaluate_report(revision_pct=-0.10, upside_pct=None, config=CFG)
        self.assertLess(a.score, -0.2)
        self.assertEqual(a.direction, "negative")

    def test_typical_upside_alone_stays_neutral_buy_bias_buffer(self):
        # 전형적 25% upside 만으로는 방향을 못 넘긴다(매수 편향 완충) — 리비전이 진짜 방향을 몬다.
        a = evaluate_report(revision_pct=None, upside_pct=0.25, config=CFG)
        self.assertTrue(a.has_signal)
        self.assertLess(abs(a.score), 0.2)
        self.assertEqual(a.direction, "neutral")

    def test_revision_and_upside_combine(self):
        a = evaluate_report(revision_pct=0.10, upside_pct=0.25, config=CFG)
        self.assertEqual(a.direction, "positive")
        # 리비전(주) + upside(보조) 합
        self.assertGreater(a.score, evaluate_report(revision_pct=0.10, upside_pct=None, config=CFG).score)

    def test_no_inputs_is_unknown_no_signal(self):
        a = evaluate_report(revision_pct=None, upside_pct=None, config=CFG)
        self.assertFalse(a.has_signal)
        self.assertEqual(a.direction, "unknown")
        self.assertEqual(a.score, 0.0)
        self.assertIn("no_valuation_signal", a.risk_flags)

    def test_score_is_bounded(self):
        a = evaluate_report(revision_pct=10.0, upside_pct=10.0, config=CFG)
        self.assertLessEqual(a.score, 1.0)
        self.assertGreaterEqual(a.score, -1.0)


if __name__ == "__main__":
    unittest.main()
