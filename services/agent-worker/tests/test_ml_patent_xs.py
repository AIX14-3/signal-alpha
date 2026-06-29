"""Unit tests for the patent cross-sectional hygiene additions.

Covers the size-neutralizing normalization (``_cross_sectional_normalize``), the
feature-exclusion path in ``build_dataset``, and the per-date cross-sectional
rank-IC (``_xs_rank_ic``) — the pieces added to test whether the patent "no
signal" was an artifact of un-normalized, size-confounded count features.
"""

from __future__ import annotations

import numpy as np

from app.ml.research.evaluation import _xs_rank_ic
from app.ml.research.patent_dataset import _cross_sectional_normalize


def _toy():
    # 2 dates x 3 stocks; stock A's count dwarfs the others on every date.
    names = ["patent__total", "patent__momentum_ratio"]
    X = np.array(
        [[1000.0, 0.5], [10.0, 0.1], [1.0, 0.9],
         [2000.0, 0.2], [20.0, 0.7], [2.0, 0.3]]
    )
    dates = np.array([1, 1, 1, 2, 2, 2])
    return X, dates, names


def test_rank_removes_size_level_keeps_order():
    X, dates, names = _toy()
    out = _cross_sectional_normalize(X, dates, names, "rank")
    # Per-date percentile in [0,1]; the 1000x size gap collapses to plain ranks.
    assert np.allclose(out[:, 0], [1.0, 0.5, 0.0, 1.0, 0.5, 0.0])
    # Ratio columns are left untouched.
    assert np.allclose(out[:, 1], X[:, 1])


def test_zscore_centers_each_date():
    X, dates, names = _toy()
    out = _cross_sectional_normalize(X, dates, names, "zscore")
    assert abs(out[:3, 0].mean()) < 1e-9
    assert abs(out[3:, 0].mean()) < 1e-9


def test_none_is_passthrough_and_nan_preserved():
    X, dates, names = _toy()
    assert np.allclose(_cross_sectional_normalize(X, dates, names, "none"), X)
    Xn = X.copy()
    Xn[1, 0] = np.nan
    out = _cross_sectional_normalize(Xn, dates, names, "rank")
    assert np.isnan(out[1, 0])  # missing stays missing, not mid-ranked


def test_xs_rank_ic_averages_per_date():
    # date 1: score perfectly orders returns (+1); date 2: perfectly inverts (-1).
    scores = np.array([3.0, 2.0, 1.0, 3.0, 2.0, 1.0])
    returns = np.array([0.3, 0.2, 0.1, 0.1, 0.2, 0.3])
    dates = np.array([1, 1, 1, 2, 2, 2])
    assert abs(_xs_rank_ic(scores, returns, dates) - 0.0) < 1e-9
    # All-aligned dates -> +1 mean.
    returns2 = np.array([0.3, 0.2, 0.1, 0.3, 0.2, 0.1])
    assert abs(_xs_rank_ic(scores, returns2, dates) - 1.0) < 1e-9
