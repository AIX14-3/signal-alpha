"""Unit tests for the multi-source fusion join helpers (pure logic, no DB).

Covers the two pieces that make ``load_fusion`` correct: the (stock_id, date) ->
row index map used for the inner join, and the cross-sectional rank-all transform
that puts every source's features on a common [0,1] per-date scale before fusion.
"""

from __future__ import annotations

import numpy as np

from app.ml.research.datalab_dataset import Dataset
from app.ml.research.fusion_db import _index_dataset, _rank_all_cross_sectional


def _ds(stock_ids, dates) -> Dataset:
    n = len(stock_ids)
    return Dataset(
        X=np.zeros((n, 1)),
        y=np.zeros(n, dtype=int),
        excess_returns=np.zeros(n),
        dates=np.array(dates, dtype=int),
        stock_ids=np.array(stock_ids, dtype=int),
        feature_names=["f"],
        dropped={},
    )


def test_index_dataset_maps_stock_date_to_row():
    ds = _ds([10, 20, 10], [100, 100, 101])
    idx = _index_dataset(ds)
    assert idx[(10, 100)] == 0
    assert idx[(20, 100)] == 1
    assert idx[(10, 101)] == 2
    # The join relies on (stock, date) being unique per source.
    assert len(idx) == 3


def test_rank_all_cross_sectional_ranks_every_column_within_date():
    # 2 dates x 3 stocks; both columns differ in scale (counts vs ratios).
    X = np.array(
        [[1000.0, 0.5], [10.0, 0.1], [1.0, 0.9],
         [2000.0, 0.2], [20.0, 0.7], [2.0, 0.3]]
    )
    dates = np.array([1, 1, 1, 2, 2, 2])
    out = _rank_all_cross_sectional(X, dates)
    # Every column becomes a per-date percentile in [0,1] — scale erased.
    assert np.allclose(out[:, 0], [1.0, 0.5, 0.0, 1.0, 0.5, 0.0])
    assert np.allclose(out[:, 1], [0.5, 0.0, 1.0, 0.0, 1.0, 0.5])


def test_rank_all_preserves_nan_and_skips_thin_dates():
    X = np.array([[5.0], [np.nan], [1.0], [9.0]])
    dates = np.array([1, 1, 1, 2])  # date 2 has a single stock -> left as-is
    out = _rank_all_cross_sectional(X, dates)
    assert np.isnan(out[1, 0])              # missing stays missing
    assert out[0, 0] == 1.0 and out[2, 0] == 0.0  # ranked among the 2 present
    assert out[3, 0] == 9.0                 # singleton date untouched


def test_rank_all_empty_matrix_is_safe():
    out = _rank_all_cross_sectional(np.empty((0, 0)), np.array([], dtype=int))
    assert out.size == 0
