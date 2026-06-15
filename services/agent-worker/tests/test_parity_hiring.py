"""Unit tests for the pure bucketing/comparison logic in parity_hiring.

The DB-touching parts (legacy capture, new loader) are integration-only; here we
pin the classification + aggregation that decides MATCH/MISMATCH/PARTIAL.
"""
from __future__ import annotations

from parity_hiring import (
    DOWN,
    FLAT,
    NO_DATA,
    UP,
    legacy_bucket,
    new_bucket,
    summarize,
    verdict,
)


class TestLegacyBucket:
    def test_spike_is_up(self):
        assert legacy_bucket(180.0, True) == UP

    def test_high_relative_strength_is_up_even_without_spike_flag(self):
        # at/above up_at counts as UP regardless of the stored is_spike bool
        assert legacy_bucket(150.0, False) == UP

    def test_below_baseline_is_down(self):
        assert legacy_bucket(70.0, False) == DOWN

    def test_around_baseline_is_flat(self):
        assert legacy_bucket(120.0, False) == FLAT

    def test_no_row_is_no_data(self):
        assert legacy_bucket(None, False) == NO_DATA

    def test_thresholds_are_configurable(self):
        # widen DOWN to <90 and UP to >=130
        assert legacy_bucket(95.0, False, down_below=90.0, up_at=130.0) == FLAT
        assert legacy_bucket(85.0, False, down_below=90.0, up_at=130.0) == DOWN
        assert legacy_bucket(130.0, False, down_below=90.0, up_at=130.0) == UP


class TestNewBucket:
    def test_direction_mapping(self):
        assert new_bucket("positive") == UP
        assert new_bucket("negative") == DOWN
        assert new_bucket("neutral") == FLAT

    def test_unknown_or_missing_is_no_data(self):
        assert new_bucket("unknown") == NO_DATA
        assert new_bucket(None) == NO_DATA


class TestVerdict:
    def test_match(self):
        assert verdict(UP, UP) == "MATCH"

    def test_mismatch(self):
        assert verdict(UP, DOWN) == "MISMATCH"

    def test_partial_when_either_side_missing(self):
        assert verdict(NO_DATA, UP) == "PARTIAL"
        assert verdict(DOWN, NO_DATA) == "PARTIAL"


class TestSummarize:
    def test_counts_and_rate_over_comparable_only(self):
        rows = [
            {"verdict": "MATCH"},
            {"verdict": "MATCH"},
            {"verdict": "MISMATCH"},
            {"verdict": "PARTIAL"},  # excluded from agreement_rate denominator
        ]
        s = summarize(rows)
        assert s["total"] == 4
        assert s["match"] == 2
        assert s["mismatch"] == 1
        assert s["partial"] == 1
        assert s["comparable"] == 3
        assert s["agreement_rate"] == 2 / 3

    def test_rate_is_none_when_nothing_comparable(self):
        s = summarize([{"verdict": "PARTIAL"}, {"verdict": "PARTIAL"}])
        assert s["comparable"] == 0
        assert s["agreement_rate"] is None
