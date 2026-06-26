"""Tests for the ML bake-off harness.

The two things that MUST be correct or the whole exercise is misleading:
1. Label construction — neutral band drops tiny moves, excess return strips the
   benchmark, is_hit matches the backtest contract.
2. The walk-forward split never leaks the future into training.

Model-quality assertions are intentionally loose: on the (easy) synthetic signal
real models must merely beat the majority baseline. We assert the harness can
*tell them apart*, not specific accuracies.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.ml.research import labels
from app.ml.research.evaluation import walk_forward_folds
from app.ml.research.features import build_feature_row, feature_matrix


# --- labels -----------------------------------------------------------------

def test_neutral_band_drops_tiny_moves():
    lab = labels.make_label(
        stock_return_pct=0.2, benchmark_return_pct=0.0, neutral_band_pct=0.3
    )
    assert lab.in_neutral_band is True
    assert lab.y_direction is None  # must be dropped, never coerced to 0/1


def test_excess_return_strips_benchmark_and_sets_direction():
    # Stock +1.0 but market +1.5 -> excess is negative -> a DOWN label.
    lab = labels.make_label(
        stock_return_pct=1.0, benchmark_return_pct=1.5, neutral_band_pct=0.1
    )
    assert lab.excess_return_pct == pytest.approx(-0.5)
    assert lab.y_direction == 0
    assert lab.in_neutral_band is False


def test_cross_sectional_excess_centers_on_universe_mean():
    excess = labels.cross_sectional_excess({1: 2.0, 2: 0.0, 3: -2.0})
    assert excess == pytest.approx({1: 2.0, 2: 0.0, 3: -2.0})  # mean 0 -> unchanged
    shifted = labels.cross_sectional_excess({1: 3.0, 2: 1.0})  # mean 2
    assert shifted == pytest.approx({1: 1.0, 2: -1.0})


def test_is_hit_matches_contract():
    assert labels.is_hit("positive", 0.5) is True
    assert labels.is_hit("positive", -0.5) is False
    assert labels.is_hit("negative", -0.5) is True
    assert labels.is_hit("neutral", 0.5) is False  # non-directional never hits


def test_negative_band_rejected():
    with pytest.raises(ValueError):
        labels.make_label(
            stock_return_pct=1.0, benchmark_return_pct=0.0, neutral_band_pct=-1.0
        )


# --- features ---------------------------------------------------------------

def test_build_feature_row_prefixes_flattens_and_coerces():
    row = build_feature_row(
        "hiring",
        {"momentum_pct": 0.4, "is_spike": True, "prior_avg": None},
        rule_score=0.25,
    )
    assert row["hiring__momentum_pct"] == 0.4
    assert row["hiring__is_spike"] == 1.0  # bool -> float
    assert np.isnan(row["hiring__prior_avg"])  # None -> nan (missing)
    assert row["hiring__rule_score"] == 0.25


def test_feature_matrix_aligns_sparse_rows():
    X, names = feature_matrix(
        [{"a": 1.0}, {"b": 2.0}, {"a": 3.0, "b": 4.0}]
    )
    assert names == ["a", "b"]
    assert np.isnan(X[0][1]) and np.isnan(X[1][0])  # missing cells -> nan
    assert X[2] == [3.0, 4.0]


# --- walk-forward leakage guard --------------------------------------------

def test_walk_forward_never_trains_on_the_future():
    # 6 distinct dates, several rows each (shuffled order).
    dates = np.array([3, 1, 2, 1, 5, 4, 6, 2, 3, 6, 4, 5])
    folds = walk_forward_folds(dates, n_folds=3)
    assert len(folds) == 3
    for fold in folds:
        train_dates = dates[fold.train_idx]
        test_dates = dates[fold.test_idx]
        # The crux: every training date is strictly before every test date.
        assert train_dates.max() < test_dates.min()


def test_walk_forward_window_expands():
    dates = np.repeat(np.arange(8), 2)
    folds = walk_forward_folds(dates, n_folds=3)
    sizes = [len(f.train_idx) for f in folds]
    assert sizes == sorted(sizes) and sizes[0] < sizes[-1]  # monotonically growing


def test_walk_forward_requires_enough_dates():
    with pytest.raises(ValueError):
        walk_forward_folds(np.array([1, 1, 2, 2]), n_folds=5)
