"""Unit tests for the patent-embedding research harness (offline, no DB/network).

Covers the four identity-leak defenses and the non-directional targets:
- within-firm demean removes firm identity (constant-per-firm embedding → 0);
- delta (cosine move / vector-diff norm) correctness;
- fold-fit PCA is fit on TRAIN only (no test leakage into the basis);
- embargo split drops train rows within ``embargo`` of the test start;
- identity control emits firm-only features (dummies, optional firm-mean block);
- magnitude targets (forward realized vol, abnormal volume z) + within-firm demean.

All data is synthetic/fake. The embedder is a deterministic fake so no network is
touched. permutation/BH/eval metrics are exercised via the EXISTING modules (not
re-implemented here).
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from app.ml.research.datalab_dataset import PriceSeries
from app.ml.research.evaluation import embargo_folds, walk_forward_folds
from app.ml.research.labels_magnitude import (
    VolumeSeries,
    abnormal_volume_z,
    demean_within_firm,
    forward_realized_vol,
)
from app.ml.research.patent_embed_dataset import (
    FoldPCA,
    apply_demean,
    build_embed_observations,
    build_feature_set,
    build_identity_control,
    fit_within_firm_demean,
    pooled_embedding,
)


class FakeEmbedder:
    """Deterministic embedder. Each patent row carries its own vector in ``vec``.

    Falls back to a hash-derived vector when no ``vec`` is present, so text-only
    rows still embed reproducibly without any network.
    """

    def __init__(self, dim: int = 8):
        self.dim = dim

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        out = []
        for t in texts:
            h = abs(hash(t)) % 1000
            rng = np.random.default_rng(h)
            out.append(rng.normal(size=self.dim))
        return np.array(out, dtype=float)


class VecEmbedder:
    """Embedder that returns a fixed vector encoded in the text prefix 'V:'.

    Text form ``"V:1.0,2.0,3.0"`` → vector [1,2,3]. Lets tests control pooled
    embeddings exactly.
    """

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        out = []
        for t in texts:
            body = t.split("V:", 1)[1].split("\n", 1)[0]
            out.append([float(x) for x in body.split(",")])
        return np.array(out, dtype=float)


def _row(pub: date, vec: list[float]) -> dict:
    return {
        "publication_date": pub,
        "title": "V:" + ",".join(str(v) for v in vec),
        "abstract": "",
    }


# --------------------------------------------------------------------------- #
# pooling
# --------------------------------------------------------------------------- #
def test_pooled_embedding_is_mean():
    rows = [_row(date(2024, 1, 1), [1.0, 0.0]), _row(date(2024, 1, 2), [3.0, 4.0])]
    vec = pooled_embedding(rows, VecEmbedder())
    assert np.allclose(vec, [2.0, 2.0])


def test_pooled_embedding_none_on_empty_text():
    rows = [{"publication_date": date(2024, 1, 1), "title": "", "abstract": ""}]
    assert pooled_embedding(rows, VecEmbedder()) is None


# --------------------------------------------------------------------------- #
# Defense 2: within-firm demean removes identity
# --------------------------------------------------------------------------- #
def test_within_firm_demean_zeros_constant_firm():
    # Firm 1: always [5,5]; firm 2: always [-2,-2]. Constant per firm → identity.
    emb = np.array([[5.0, 5.0], [5.0, 5.0], [-2.0, -2.0], [-2.0, -2.0]])
    ids = np.array([1, 1, 2, 2])
    fit_mask = np.ones(4, dtype=bool)
    means, gmean = fit_within_firm_demean(emb, ids, fit_mask)
    out = apply_demean(emb, ids, means, gmean)
    assert np.allclose(out, 0.0)  # identity fully removed


def test_within_firm_demean_keeps_deviation():
    # Firm 1 mean is [1,1]; the second obs deviates by [+1,+1].
    emb = np.array([[0.0, 0.0], [2.0, 2.0]])
    ids = np.array([1, 1])
    means, gmean = fit_within_firm_demean(emb, ids, np.ones(2, dtype=bool))
    out = apply_demean(emb, ids, means, gmean)
    assert np.allclose(out, [[-1.0, -1.0], [1.0, 1.0]])


def test_demean_fit_on_train_only_no_leakage():
    # Test row has a wild value; if it leaked into the mean the train demean shifts.
    emb = np.array([[1.0], [3.0], [999.0]])
    ids = np.array([1, 1, 1])
    fit_mask = np.array([True, True, False])  # last row is test
    means, gmean = fit_within_firm_demean(emb, ids, fit_mask)
    # Firm mean uses only train rows (1,3) → 2.0, NOT (1+3+999)/3.
    assert np.allclose(means[1], [2.0])
    out = apply_demean(emb, ids, means, gmean)
    assert np.allclose(out[:2, 0], [-1.0, 1.0])


def test_demean_unseen_firm_falls_back_to_global():
    emb = np.array([[10.0], [20.0]])
    ids = np.array([1, 2])
    fit_mask = np.array([True, False])  # firm 2 never in fit set
    means, gmean = fit_within_firm_demean(emb, ids, fit_mask)
    out = apply_demean(emb, ids, means, gmean)
    assert np.allclose(gmean, [10.0])
    assert np.allclose(out[1, 0], 20.0 - 10.0)  # used global mean


# --------------------------------------------------------------------------- #
# Defense 3: delta features
# --------------------------------------------------------------------------- #
def test_delta_first_obs_is_zero_then_moves():
    dates = [date(2024, 1, 1), date(2024, 1, 8)]
    # Window t1: [1,0]; window t2: [0,1] (orthogonal → cosine move = 1.0).
    rows = [_row(dates[0], [1.0, 0.0]), _row(dates[1], [0.0, 1.0])]
    obs = build_embed_observations(
        patent_rows_by_stock={1: rows},
        signal_dates_by_stock={1: dates},
        embedder=VecEmbedder(),
        lookback_days=3,  # each window sees exactly its own filing
    )
    assert len(obs) == 2
    # First obs: no previous → 0.
    assert np.allclose(obs.delta[0], [0.0, 0.0])
    # Orthogonal move: cosine move = 1 - 0 = 1; norm = sqrt(2).
    assert abs(obs.delta[1, 0] - 1.0) < 1e-9
    assert abs(obs.delta[1, 1] - np.sqrt(2)) < 1e-9


def test_delta_identical_content_is_zero_move():
    dates = [date(2024, 1, 1), date(2024, 1, 8)]
    rows = [_row(dates[0], [2.0, 1.0]), _row(dates[1], [2.0, 1.0])]
    obs = build_embed_observations(
        patent_rows_by_stock={1: rows},
        signal_dates_by_stock={1: dates},
        embedder=VecEmbedder(),
        lookback_days=3,
    )
    assert abs(obs.delta[1, 0]) < 1e-9  # same direction → no cosine move
    assert abs(obs.delta[1, 1]) < 1e-9  # identical vector → zero norm


# --------------------------------------------------------------------------- #
# Defense 4: fold-fit PCA (no leakage)
# --------------------------------------------------------------------------- #
def test_fold_pca_fit_on_train_only():
    rng = np.random.default_rng(0)
    X_train = rng.normal(size=(50, 8))
    X_test = rng.normal(size=(10, 8))
    pca = FoldPCA(n_components=4).fit(X_train)
    comps = pca._pca.components_.copy()
    # Transforming test must NOT change the fitted basis (no re-fit leakage).
    _ = pca.transform(X_test)
    assert np.allclose(pca._pca.components_, comps)
    assert pca.transform(X_test).shape == (10, 4)


def test_fold_pca_components_capped_by_data():
    X = np.random.default_rng(1).normal(size=(3, 8))
    pca = FoldPCA(n_components=16).fit(X)  # only 3 rows → <=3 comps
    assert pca._pca.n_components_ <= 3


def test_fold_pca_transform_before_fit_raises():
    import pytest

    with pytest.raises(RuntimeError):
        FoldPCA().transform(np.zeros((2, 3)))


# --------------------------------------------------------------------------- #
# Defense: embargo split
# --------------------------------------------------------------------------- #
def test_embargo_drops_near_boundary_train_rows():
    # 12 distinct dates (ordinals 0..11), 2 folds.
    dates = np.arange(12)
    base = walk_forward_folds(dates, n_folds=2)
    embar = embargo_folds(dates, n_folds=2, embargo=3)
    total_dropped = 0
    # First fold's test block starts at some date; embargo removes train rows within 3.
    for b, e in zip(base, embar):
        test_start = int(dates[b.test_idx].min())
        # every retained train row must be strictly < test_start - embargo
        assert np.all(dates[e.train_idx] < test_start - 3)
        # and the dropped ones are exactly those in [test_start-3, test_start)
        dropped = set(b.train_idx) - set(e.train_idx)
        total_dropped += len(dropped)
        for i in dropped:
            assert test_start - 3 <= dates[i] < test_start
    # non-vacuous: the embargo must actually remove at least one near-boundary row
    assert total_dropped > 0


def test_embargo_zero_equals_walk_forward():
    dates = np.arange(12)
    base = walk_forward_folds(dates, n_folds=2)
    embar = embargo_folds(dates, n_folds=2, embargo=0)
    for b, e in zip(base, embar):
        assert np.array_equal(b.train_idx, e.train_idx)
        assert np.array_equal(b.test_idx, e.test_idx)


# --------------------------------------------------------------------------- #
# comparison arms + identity control
# --------------------------------------------------------------------------- #
def test_build_feature_set_concat_prefixes_disjoint():
    num = np.array([[1.0, 2.0]])
    emb = np.array([[9.0]])
    X, names = build_feature_set(
        mode="concat",
        numeric_X=num,
        numeric_names=["patent__total", "patent__ratio"],
        embed_X=emb,
        embed_names=["patent_emb__dim_0"],
    )
    assert X.shape == (1, 3)
    assert names == ["patent__total", "patent__ratio", "patent_emb__dim_0"]
    # no name collides across the two families
    assert all(n.startswith("patent__") or n.startswith("patent_emb__") for n in names)


def test_identity_control_is_firm_only():
    ids = np.array([7, 7, 42])
    X, names = build_identity_control(ids)
    # one-hot firm dummies: firm 7 rows share a column; row 3 is firm 42.
    assert X.shape == (3, 2)
    assert np.array_equal(X[0], X[1])  # same firm → identical features
    assert not np.array_equal(X[0], X[2])
    assert names == ["patent_emb__firm_7", "patent_emb__firm_42"]


def test_identity_control_with_firm_mean_block():
    ids = np.array([1, 2])
    X, names = build_identity_control(
        ids, firm_mean_embeddings={1: np.array([0.5, 0.5]), 2: np.array([9.0, 9.0])}
    )
    # 2 firm dummies + 2 mean dims
    assert X.shape == (2, 4)
    assert names[-2:] == ["patent_emb__firm_mean_0", "patent_emb__firm_mean_1"]


# --------------------------------------------------------------------------- #
# Non-directional (magnitude) targets
# --------------------------------------------------------------------------- #
def _price_series(start: date, closes: list[float]) -> PriceSeries:
    pairs = [(start + timedelta(days=i), c) for i, c in enumerate(closes)]
    return PriceSeries.from_pairs(pairs)


def test_forward_realized_vol_sign_agnostic():
    up = _price_series(date(2024, 1, 1), [100, 110, 121, 133.1])
    down = _price_series(date(2024, 1, 1), [100, 90, 81, 72.9])
    # both move ~10%/day in magnitude → equal realized vol despite opposite signs
    v_up = forward_realized_vol(up, date(2024, 1, 1), 3)
    v_down = forward_realized_vol(down, date(2024, 1, 1), 3)
    assert v_up is not None and abs(v_up - v_down) < 1e-9


def test_forward_realized_vol_none_when_short():
    p = _price_series(date(2024, 1, 1), [100, 101])
    assert forward_realized_vol(p, date(2024, 1, 1), 3) is None


def test_abnormal_volume_z_positive_on_spike():
    # 20 flat baseline days then a forward spike.
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(25)]
    vols = [100.0 + (i % 2) for i in range(21)] + [1000.0, 1000.0, 1000.0, 1000.0]
    vs = VolumeSeries.from_pairs(list(zip(dates, vols)))
    z = abnormal_volume_z(vs, dates[20], horizon_sessions=3, baseline_sessions=20)
    assert z is not None and z > 5.0  # big spike vs quiet baseline


def test_abnormal_volume_z_none_on_zero_variance_baseline():
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(25)]
    vols = [100.0] * 21 + [500.0, 500.0, 500.0, 500.0]  # flat baseline → std 0
    vs = VolumeSeries.from_pairs(list(zip(dates, vols)))
    assert abnormal_volume_z(vs, dates[20], 3, baseline_sessions=20) is None


def test_magnitude_demean_within_firm_train_only():
    y = np.array([1.0, 3.0, 100.0])  # firm 1: 1,3 (train), 100 (test)
    ids = np.array([1, 1, 1])
    fit = np.array([True, True, False])
    out = demean_within_firm(y, ids, fit_mask=fit)
    # mean = 2 (train only) → [-1, 1, 98]
    assert np.allclose(out, [-1.0, 1.0, 98.0])
