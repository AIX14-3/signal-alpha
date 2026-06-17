"""Characterization test for the DataLab momentum/change double-count (L2).

Both ``momentum`` (window-level growth, recent-vs-prior ratio) and ``change``
(averaged per-row ``change_pct``) measure the SAME underlying phenomenon —
search-trend growth — yet the final score in ``rules.evaluate_indicators`` sums
them (``momentum + spike + change + risk``). So a consistently rising trend is
counted twice.

These tests PIN that current behavior (they are GREEN against current code).
They do NOT fix it: the corrective change (remove ``change`` / reweight /
orthogonalize) is a product decision pending the scoring redesign. If a future
change makes ``momentum`` and ``change`` orthogonal (or drops one), these tests
are expected to change — that is the signal that the double-count was addressed.
"""

import unittest

from app.analyzers.config import DataLabRuleConfig
from app.analyzers.datalab.indicators import DataLabIndicators
from app.analyzers.datalab.rules import (
    _change_component,
    _momentum_component,
    evaluate_indicators,
)

CONFIG = DataLabRuleConfig(
    lookback_days=30,
    min_observations=5,
    min_prior_observations=5,
    momentum_threshold=0.1,
    momentum_scale=0.30,
    change_scale=30.0,
    spike_threshold=0.2,
    spike_scale=0.30,
    stale_days=14,
    momentum_weight=0.6,
    spike_weight=0.2,
    change_weight=0.2,
    positive_threshold=0.2,
    negative_threshold=-0.2,
)


def _growing_indicators() -> DataLabIndicators:
    """A clean 'search trend is growing' case.

    recent avg (90) > prior avg (50) → momentum_pct = +0.8 (positive momentum).
    avg_change_pct = +40%p (positive change). Both encode the SAME growth.
    Prior window has >= min_prior_observations so the small-sample guard does
    not fire (we want the two components live, not suppressed).
    No spikes, no risk → spike and risk components are 0, isolating the pair.
    """
    return DataLabIndicators(
        observations=8,
        weighted_recent_avg=90.0,
        weighted_prior_avg=50.0,
        momentum_pct=0.8,  # (90 - 50) / 50
        spike_ratio=0.0,
        avg_change_pct=40.0,  # +40 percentage points, averaged per row
        prior_observations=5,  # >= min_prior_observations → guard off
        risk_momentum_pct=None,
        risk_prior_observations=0,
        latest_observed_date="2026-06-09",
        days_since_latest=2,
    )


class DataLabDoubleCountCharacterizationTest(unittest.TestCase):
    def test_momentum_and_change_each_contribute_positively(self):
        """Both growth components are individually positive for a rising trend."""
        indicators = _growing_indicators()

        momentum = _momentum_component(indicators, CONFIG, highlights=[])
        change = _change_component(indicators, CONFIG, highlights=[])

        self.assertGreater(momentum, 0.0, "rising trend should give positive momentum")
        self.assertGreater(change, 0.0, "rising trend should give positive change")

    def test_double_count_inflates_the_final_score(self):
        """The two growth components are ADDED, so the rising trend is counted twice.

        Pin: the final score equals momentum + change (spike/risk are 0 here),
        i.e. it is strictly larger than either growth component alone. This is the
        double-count. When the scoring redesign removes/reweights/orthogonalizes
        ``change`` vs ``momentum``, this assertion is expected to change.
        """
        indicators = _growing_indicators()

        momentum = _momentum_component(indicators, CONFIG, highlights=[])
        change = _change_component(indicators, CONFIG, highlights=[])
        assessment = evaluate_indicators(indicators, CONFIG)

        # spike == 0 and risk == 0 for this fixture, so the score is purely the
        # sum of the two growth components (below the +1.0 clamp).
        expected = round(momentum + change, 3)
        self.assertAlmostEqual(assessment.score, expected, places=3)

        # The hallmark of the double-count: the combined score exceeds EITHER
        # growth component on its own — consistent growth is credited twice.
        self.assertGreater(assessment.score, momentum)
        self.assertGreater(assessment.score, change)

    def test_change_alone_moves_the_final_score(self):
        """Holding momentum fixed, adding the change signal raises the score.

        Demonstrates ``change`` is an independent additive term (not folded into
        momentum): zeroing ``avg_change_pct`` drops the score by exactly the
        change component, even though momentum is unchanged.
        """
        with_change = _growing_indicators()

        # Same momentum, but no per-row change signal available.
        without_change = DataLabIndicators(
            observations=with_change.observations,
            weighted_recent_avg=with_change.weighted_recent_avg,
            weighted_prior_avg=with_change.weighted_prior_avg,
            momentum_pct=with_change.momentum_pct,
            spike_ratio=with_change.spike_ratio,
            avg_change_pct=None,  # change component → 0
            prior_observations=with_change.prior_observations,
            risk_momentum_pct=with_change.risk_momentum_pct,
            risk_prior_observations=with_change.risk_prior_observations,
            latest_observed_date=with_change.latest_observed_date,
            days_since_latest=with_change.days_since_latest,
        )

        change = _change_component(with_change, CONFIG, highlights=[])
        a = evaluate_indicators(with_change, CONFIG)
        b = evaluate_indicators(without_change, CONFIG)

        self.assertGreater(change, 0.0)
        # Dropping change lowers the score by exactly the change component.
        self.assertAlmostEqual(a.score, round(b.score + change, 3), places=3)


if __name__ == "__main__":
    unittest.main()
