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
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
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
    decile_spread: float
    # Regression-only (magnitude task); nan on the classification path.
    r2: float = float("nan")
    mae: float = float("nan")
    rmse: float = float("nan")


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
            "decile_spread",
            "r2",
            "mae",
            "rmse",
        ):
            mean, std = self._agg(attr)
            out[f"{attr}_mean"] = mean
            out[f"{attr}_std"] = std
        return out


def _regression_fold_metrics(pred: np.ndarray, yte: np.ndarray) -> FoldMetrics:
    """Per-fold metrics for the magnitude task: IC/Rank-IC of predicted vs realized.

    The primary selection metric is ``rank_ic`` (Spearman of predicted magnitude vs
    realized magnitude) — task-agnostic and identical in spirit to the classifier
    path's score-vs-return IC. Classification fields stay nan.
    """
    n = len(yte)
    r2 = float(r2_score(yte, pred)) if n >= 2 and np.std(yte) > 0 else float("nan")
    mae = float(mean_absolute_error(yte, pred)) if n >= 1 else float("nan")
    rmse = float(np.sqrt(mean_squared_error(yte, pred))) if n >= 1 else float("nan")
    return FoldMetrics(
        n_test=n,
        accuracy=float("nan"),
        precision=float("nan"),
        recall=float("nan"),
        f1=float("nan"),
        roc_auc=float("nan"),
        ic=_safe_corr(pearsonr, pred, yte),
        rank_ic=_safe_corr(spearmanr, pred, yte),
        decile_spread=_decile_spread(pred, yte),
        r2=r2,
        mae=mae,
        rmse=rmse,
    )


def evaluate_model(
    name: str,
    model,
    X: np.ndarray,
    y: np.ndarray,
    excess_returns: np.ndarray,
    folds: list[WalkForwardSplit],
    *,
    task: str = "direction",
) -> ModelReport:
    """Run one model across all walk-forward folds and collect per-fold metrics.

    ``task='direction'`` (default) scores a binary classifier; ``task='magnitude'``
    scores a regressor on the continuous magnitude target ``y`` and records
    regression metrics instead (Rank-IC / IC / R² / MAE / RMSE).
    """
    is_regression = task == "magnitude"
    report = ModelReport(name=name)
    for fold in folds:
        Xtr, ytr = X[fold.train_idx], y[fold.train_idx]
        Xte, yte = X[fold.test_idx], y[fold.test_idx]
        ret_te = excess_returns[fold.test_idx]
        # Classification needs both classes present; regression just needs variance
        # in the training target (a constant target teaches nothing).
        degenerate = np.std(ytr) == 0 if is_regression else len(np.unique(ytr)) < 2
        if degenerate or len(Xte) == 0:
            continue  # can't train/score a degenerate fold
        from sklearn.base import clone

        try:
            est = clone(model)
            est.fit(Xtr, ytr)
            pred = est.predict(Xte)
            score = _bullish_score(est, Xte)
        except Exception:
            # A model that can't fit/predict this (often tiny, long-horizon) fold is
            # skipped for the fold rather than crashing the whole bake-off run, e.g.
            # KNN when n_neighbors > the fold's train size. Other models/folds stand.
            continue
        if is_regression:
            report.folds.append(_regression_fold_metrics(pred, yte))
            continue
        both_classes = len(np.unique(yte)) == 2
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
                decile_spread=_decile_spread(score, ret_te),
            )
        )
    return report
