"""Time-aware evaluation: walk-forward folds + the 4-group metric panel.

Why not a plain train/test split? With market data a random split leaks the
future into the past (you'd "predict" Monday using Friday). We instead sort by
date and only ever train on rows STRICTLY BEFORE the test block — an expanding
("walk-forward") window. A date never straddles the train/test boundary.

For each model and fold we record four metric groups (see the plan):
  A. direction  — accuracy / precision / recall / f1 / roc_auc
  B. magnitude  — Information Coefficient (Pearson) and Rank-IC (Spearman) between
                  the model's bullish score and the realized excess return
  C. economic   — mean excess return of the top-decile minus bottom-decile by score
  D. robustness — every metric is reported as fold mean ± std with the sample size,
                  so a lucky single fold can't masquerade as skill.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


@dataclass(frozen=True)
class WalkForwardSplit:
    train_idx: np.ndarray
    test_idx: np.ndarray


def walk_forward_folds(dates: np.ndarray, n_folds: int = 5) -> list[WalkForwardSplit]:
    """Expanding-window folds over date-sorted rows, split at DATE boundaries.

    Unique dates are cut into ``n_folds + 1`` contiguous chunks; fold *i* trains on
    chunks ``0..i-1`` and tests on chunk *i*. Rows sharing a date are never split
    across the boundary, so there is no same-day leakage.
    """
    if n_folds < 1:
        raise ValueError("n_folds must be >= 1")
    order = np.argsort(dates, kind="stable")
    unique = np.unique(dates)
    if len(unique) < n_folds + 1:
        raise ValueError(
            f"need >= {n_folds + 1} distinct dates for {n_folds} folds, got {len(unique)}"
        )
    chunks = np.array_split(unique, n_folds + 1)
    folds: list[WalkForwardSplit] = []
    for i in range(1, n_folds + 1):
        train_dates = np.concatenate(chunks[:i])
        test_dates = chunks[i]
        train_mask = np.isin(dates, train_dates)
        test_mask = np.isin(dates, test_dates)
        # Preserve chronological order within each split (via ``order``).
        folds.append(
            WalkForwardSplit(
                train_idx=order[np.isin(order, np.where(train_mask)[0])],
                test_idx=order[np.isin(order, np.where(test_mask)[0])],
            )
        )
    return folds


def embargo_folds(
    dates: np.ndarray,
    n_folds: int = 5,
    *,
    embargo: int = 0,
) -> list[WalkForwardSplit]:
    """Walk-forward folds with an EMBARGO gap between train and test.

    Same expanding-window logic as :func:`walk_forward_folds`, but after choosing
    each fold's test dates we DROP from the train side every row whose date lies
    within ``embargo`` ordinal days *before* the test block's first date. Why this
    matters for the patent track: a feature at a train ``as_of`` is built from a
    lookback window, and its label reaches ``horizon`` (``h``) sessions forward — so
    a train row dated within ``h`` of the test start has a label/feature footprint
    that overlaps the test period. Setting ``embargo >= h`` (in the SAME unit as
    ``dates`` — ordinal days) forbids that overlap, closing the near-boundary leak
    that plain walk-forward leaves open.

    ``embargo=0`` reproduces :func:`walk_forward_folds` exactly. Dates are ordinal
    ints (as produced by the datasets); with ~1.4 calendar days per trading session
    a caller wanting ``h`` sessions of gap should pass roughly ``2 * h`` here, or
    simply ``h`` for a conservative session-count lower bound.
    """
    if embargo < 0:
        raise ValueError("embargo must be >= 0")
    base = walk_forward_folds(dates, n_folds=n_folds)
    if embargo == 0:
        return base
    out: list[WalkForwardSplit] = []
    for fold in base:
        test_dates = dates[fold.test_idx]
        test_start = int(test_dates.min())
        cutoff = test_start - embargo  # train rows must be strictly before this
        keep = dates[fold.train_idx] < cutoff
        out.append(
            WalkForwardSplit(
                train_idx=fold.train_idx[keep],
                test_idx=fold.test_idx,
            )
        )
    return out


def _bullish_score(model, X: np.ndarray) -> np.ndarray:
    """A higher-is-more-bullish score per row, however the model exposes it.

    Prefers calibrated probability, then a decision margin, then the hard label —
    so every estimator (even ones without ``predict_proba``) yields a rankable
    score for the IC/economic metrics.
    """
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(X)
        classes = list(getattr(model, "classes_", [0, 1]))
        col = classes.index(1) if 1 in classes else proba.shape[1] - 1
        return proba[:, col]
    if hasattr(model, "decision_function"):
        return np.asarray(model.decision_function(X), dtype=float)
    return np.asarray(model.predict(X), dtype=float)


def _safe_corr(fn, a: np.ndarray, b: np.ndarray) -> float:
    """Correlation that returns nan instead of raising on degenerate input."""
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    try:
        return float(fn(a, b)[0])
    except Exception:
        return float("nan")


def _xs_rank_ic(
    scores: np.ndarray, returns: np.ndarray, dates: np.ndarray
) -> float:
    """Mean of per-DATE cross-sectional rank-ICs (the textbook quant IC).

    The pooled ``rank_ic`` correlates score vs return over a whole multi-date test
    chunk, mixing the cross-sectional question ("which stock outperforms today?")
    with time-series drift. Here we instead compute one Spearman per date across
    that date's stocks, then average — so the metric answers the cross-sectional
    question cleanly. Dates with <3 stocks or no score/return variance are skipped;
    nan if none qualify.
    """
    ics: list[float] = []
    for d in np.unique(dates):
        m = dates == d
        ic = _safe_corr(spearmanr, scores[m], returns[m])
        if not np.isnan(ic):
            ics.append(ic)
    return float(np.mean(ics)) if ics else float("nan")


def _decile_spread(scores: np.ndarray, returns: np.ndarray) -> float:
    """Mean excess return of the top-decile minus the bottom-decile by score.

    The economic question: if we acted on the most-bullish 10% vs the most-bearish
    10%, how different were the realized moves? nan when there are too few rows.
    """
    n = len(scores)
    if n < 10:
        return float("nan")
    k = max(1, n // 10)
    order = np.argsort(scores)
    bottom = returns[order[:k]].mean()
    top = returns[order[-k:]].mean()
    return float(top - bottom)


@dataclass
class FoldMetrics:
    n_test: int
    accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float
    ic: float
    rank_ic: float
    rank_ic_xs: float
    decile_spread: float


@dataclass
class ModelReport:
    name: str
    folds: list[FoldMetrics] = field(default_factory=list)

    def _agg(self, attr: str) -> tuple[float, float]:
        vals = np.array([getattr(f, attr) for f in self.folds], dtype=float)
        vals = vals[~np.isnan(vals)]
        if len(vals) == 0:
            return float("nan"), float("nan")
        return float(vals.mean()), float(vals.std())

    def summary(self) -> dict[str, float]:
        out: dict[str, float] = {"n_test": sum(f.n_test for f in self.folds)}
        for attr in (
            "accuracy",
            "precision",
            "recall",
            "f1",
            "roc_auc",
            "ic",
            "rank_ic",
            "rank_ic_xs",
            "decile_spread",
        ):
            mean, std = self._agg(attr)
            out[f"{attr}_mean"] = mean
            out[f"{attr}_std"] = std
        return out


def evaluate_model(
    name: str,
    model,
    X: np.ndarray,
    y: np.ndarray,
    excess_returns: np.ndarray,
    folds: list[WalkForwardSplit],
    dates: np.ndarray | None = None,
) -> ModelReport:
    """Run one model across all walk-forward folds and collect per-fold metrics.

    ``dates`` (optional, row-aligned ordinals) enables the per-date cross-sectional
    rank-IC (``rank_ic_xs``); without it that metric is nan but everything else is
    unchanged, keeping older callers working.
    """
    report = ModelReport(name=name)
    for fold in folds:
        Xtr, ytr = X[fold.train_idx], y[fold.train_idx]
        Xte, yte = X[fold.test_idx], y[fold.test_idx]
        ret_te = excess_returns[fold.test_idx]
        if len(np.unique(ytr)) < 2 or len(Xte) == 0:
            continue  # can't train/score a degenerate fold
        from sklearn.base import clone

        est = clone(model)
        est.fit(Xtr, ytr)
        pred = est.predict(Xte)
        score = _bullish_score(est, Xte)
        both_classes = len(np.unique(yte)) == 2
        rank_ic_xs = (
            _xs_rank_ic(score, ret_te, dates[fold.test_idx])
            if dates is not None
            else float("nan")
        )
        report.folds.append(
            FoldMetrics(
                n_test=len(yte),
                accuracy=accuracy_score(yte, pred),
                precision=precision_score(yte, pred, zero_division=0),
                recall=recall_score(yte, pred, zero_division=0),
                f1=f1_score(yte, pred, zero_division=0),
                roc_auc=roc_auc_score(yte, score) if both_classes else float("nan"),
                ic=_safe_corr(pearsonr, score, ret_te),
                rank_ic=_safe_corr(spearmanr, score, ret_te),
                rank_ic_xs=rank_ic_xs,
                decile_spread=_decile_spread(score, ret_te),
            )
        )
    return report
