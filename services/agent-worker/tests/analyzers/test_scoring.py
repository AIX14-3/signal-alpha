"""Unit tests for the graded() scoring transform (pure, tanh-based)."""

import math
import unittest

from app.analyzers.scoring import graded

SCALE, W = 0.30, 0.6  # canonical momentum config


class GradedTest(unittest.TestCase):
    def test_zero_is_zero(self):
        self.assertEqual(graded(0.0, scale=SCALE, weight=W), 0.0)

    def test_small_input_small_score(self):
        # +5% → small but non-zero (no hard dead-zone), well under weight.
        val = graded(0.05, scale=SCALE, weight=W)
        self.assertGreater(val, 0.0)
        self.assertLess(val, 0.15)

    def test_knee_at_scale(self):
        # at value == scale, tanh(1) ≈ 0.7616 → 0.7616 * weight.
        self.assertAlmostEqual(graded(SCALE, scale=SCALE, weight=W), W * math.tanh(1.0), places=6)

    def test_monotonic_increasing(self):
        a = graded(0.10, scale=SCALE, weight=W)
        b = graded(0.30, scale=SCALE, weight=W)
        c = graded(1.00, scale=SCALE, weight=W)
        self.assertLess(a, b)
        self.assertLess(b, c)

    def test_soft_saturation_never_reaches_weight(self):
        # Even a large input stays strictly below the weight ceiling (asymptotic).
        big = graded(5.0, scale=SCALE, weight=W)
        self.assertLess(big, W)
        self.assertGreater(big, 0.59)  # but close

    def test_sign_preserved_symmetric(self):
        self.assertAlmostEqual(
            graded(-0.30, scale=SCALE, weight=W), -graded(0.30, scale=SCALE, weight=W), places=9
        )

    def test_degenerate_scale_is_sign_step(self):
        self.assertEqual(graded(0.2, scale=0.0, weight=0.6), 0.6)
        self.assertEqual(graded(-0.2, scale=0.0, weight=0.6), -0.6)
        self.assertEqual(graded(0.0, scale=0.0, weight=0.6), 0.0)


if __name__ == "__main__":
    unittest.main()
