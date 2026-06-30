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

from app.ml import labels
from app.ml.evaluation import walk_forward_folds
from app.ml.features import build_feature_row, feature_matrix


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


# --- magnitude label (non-directional, the validated attention survivor) ----

def test_forward_realized_volatility_matches_pstdev_of_log_returns():
    import math
    import statistics

    closes = [100.0, 110.0, 99.0, 108.9]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    expected = statistics.pstdev(rets) * 100.0
    assert labels.forward_realized_volatility(closes) == pytest.approx(expected)


def test_forward_realized_volatility_needs_two_returns():
    assert labels.forward_realized_volatility([100.0]) is None  # 0 returns
    assert labels.forward_realized_volatility([100.0, 101.0]) is None  # only 1 return


def test_forward_abnormal_volume_is_forward_over_baseline():
    # forward mean 300, baseline mean 100 -> 3.0x abnormal volume
    assert labels.forward_abnormal_volume([200.0, 400.0], [100.0, 100.0]) == pytest.approx(3.0)


def test_forward_abnormal_volume_guards_empty_and_zero_baseline():
    assert labels.forward_abnormal_volume([], [100.0]) is None
    assert labels.forward_abnormal_volume([100.0], []) is None
    assert labels.forward_abnormal_volume([100.0], [0.0]) is None


def test_make_magnitude_label_is_unsigned_and_bundles_both():
    lab = labels.make_magnitude_label(
        forward_closes=[100.0, 90.0, 99.0],      # a DOWN move...
        forward_volumes=[300.0, 300.0],
        baseline_volumes=[100.0, 100.0],
    )
    # magnitude is direction-agnostic: a down move still yields positive vol/volume.
    assert lab.fwd_volatility is not None and lab.fwd_volatility > 0
    assert lab.fwd_abn_volume == pytest.approx(3.0)


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


def test_evaluate_model_skips_a_model_that_errors_on_a_fold():
    # A model failing on a (typically tiny, long-horizon) fold must be skipped for
    # that fold, not crash the whole bake-off — e.g. KNN when n_neighbors > train size.
    from sklearn.base import BaseEstimator, ClassifierMixin

    from app.ml.evaluation import evaluate_model

    class _Exploding(BaseEstimator, ClassifierMixin):
        def fit(self, X, y):
            raise ValueError("cannot fit this fold")

        def predict(self, X):  # pragma: no cover - never reached
            return np.zeros(len(X))

    dates = np.repeat(np.arange(8), 3)
    n = len(dates)
    X = np.random.default_rng(0).normal(size=(n, 4))
    y = np.tile([0, 1, 1], 8)
    excess = np.random.default_rng(1).normal(size=n)
    folds = walk_forward_folds(dates, n_folds=3)

    report = evaluate_model("exploding", _Exploding(), X, y, excess, folds)

    assert report.folds == []  # every fold skipped, no exception raised
    assert np.isnan(report.summary()["rank_ic_mean"])  # degrades to nan, not a crash


# --- magnitude regression path ---------------------------------------------

def test_priceseries_forward_windows_are_point_in_time():
    from datetime import date

    from app.ml.datalab_dataset import PriceSeries

    days = [date(2021, 1, d) for d in range(1, 11)]  # 10 trading days
    closes = [100.0 + i for i in range(10)]
    volumes = [10.0 + i for i in range(10)]  # 10,11,...,19
    ps = PriceSeries.from_rows(list(zip(days, closes, volumes)))

    as_of = days[3]  # index 3
    # forward closes = entry + h forward (closes[3..3+2] inclusive).
    assert ps.forward_closes(as_of, 2) == closes[3:6]
    # forward volumes are STRICTLY after as_of (vol[4..5]); baseline STRICTLY before.
    assert ps.forward_volumes(as_of, 2) == volumes[4:6]
    assert ps.baseline_volumes(as_of, back=3) == volumes[0:3]
    # not enough forward sessions / no prior session -> None (never fabricated).
    assert ps.forward_closes(days[9], 2) is None
    assert ps.forward_volumes(days[9], 2) is None
    assert ps.baseline_volumes(days[0], back=3) is None


def test_priceseries_without_volume_returns_none_for_volume_windows():
    from datetime import date

    from app.ml.datalab_dataset import PriceSeries

    days = [date(2021, 1, d) for d in range(1, 6)]
    ps = PriceSeries.from_pairs(list(zip(days, [100.0, 101.0, 102.0, 103.0, 104.0])))
    assert ps.forward_volumes(days[1], 2) is None  # closes-only series has no volume
    assert ps.baseline_volumes(days[2], back=2) is None


def test_regressor_registry_has_baselines_and_fits():
    from app.ml.models import build_regressor_registry

    reg = build_regressor_registry(seed=0)
    assert "baseline_mean" in reg and "baseline_median" in reg
    assert "lda" not in reg and "naive_bayes" not in reg  # no regression form
    rng = np.random.default_rng(0)
    X = rng.normal(size=(40, 3))
    y = X[:, 0] * 2.0 + rng.normal(scale=0.1, size=40)  # continuous target
    est = reg["ridge"]
    est.fit(X, y)
    pred = est.predict(X)
    assert pred.shape == (40,) and np.isfinite(pred).all()


def test_evaluate_model_magnitude_emits_regression_metrics():
    from app.ml.evaluation import evaluate_model, walk_forward_folds
    from app.ml.models import build_regressor_registry

    dates = np.repeat(np.arange(8), 6)
    n = len(dates)
    rng = np.random.default_rng(0)
    X = rng.normal(size=(n, 3))
    y = X[:, 0] * 3.0 + rng.normal(scale=0.2, size=n)  # continuous magnitude
    folds = walk_forward_folds(dates, n_folds=3)

    report = evaluate_model(
        "ridge", build_regressor_registry(seed=0)["ridge"], X, y, y, folds, task="magnitude"
    )
    s = report.summary()
    assert report.folds  # at least one fold scored
    assert not np.isnan(s["rank_ic_mean"])  # predicted vs realized magnitude tracks
    assert not np.isnan(s["r2_mean"])
    assert np.isnan(s["accuracy_mean"])  # classification fields stay nan on this path


def test_build_magnitude_dataset_emits_continuous_unsigned_target():
    from datetime import date, timedelta

    from app.ml.datalab_dataset import PriceSeries
    from app.ml.magnitude_dataset import build_magnitude_dataset

    base = date(2021, 1, 1)
    days = [base + timedelta(days=i) for i in range(20)]
    closes = [100.0 + (i % 3) for i in range(20)]  # some wiggle for volatility
    volumes = [10.0 + i for i in range(20)]
    prices = PriceSeries.from_rows(list(zip(days, closes, volumes)))
    # one search observation per day, rising — gives a defined rolling z after history.
    search = [(d, float(i)) for i, d in enumerate(days)]

    ds = build_magnitude_dataset(
        search_by_ticker={"AAA": search},
        prices_by_ticker={"AAA": prices},
        signal_dates_by_ticker={"AAA": days},
        target="volatility",
        horizon_sessions=2,
        baseline_back=4,
        mom_lag=1,
        win=4,  # tiny window so the short fixture yields samples
    )
    assert len(ds) > 0
    assert ds.y.dtype == float and np.all(ds.y >= 0)  # magnitude is unsigned
    assert "magnitude__abn" in ds.feature_names
    # excess_returns mirrors the magnitude target (used for IC), not a signed return.
    assert np.allclose(ds.y, ds.excess_returns)
