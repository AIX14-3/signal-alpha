"""Hit-rate and comparison metrics.

All functions take aligned numpy arrays. The honest baselines are the whole
point: a 53% hit-rate only matters relative to how often the stock simply went
up (buy-and-hold / majority class).
"""

from __future__ import annotations

import numpy as np


def directional(pred: np.ndarray, label: np.ndarray, ret: np.ndarray) -> dict:
    """Evaluate {-1,+1} predictions against labels, ignoring flat/NaN rows."""
    mask = np.isfinite(label) & (label != 0) & np.isfinite(ret)
    pred, label, ret = pred[mask], label[mask], ret[mask]
    n = int(len(label))
    if n == 0:
        return {"n": 0}

    acc = float(np.mean(pred == label))
    up_rate = float(np.mean(label > 0))          # buy-and-hold / always-up accuracy
    majority = max(up_rate, 1 - up_rate)         # always-predict-dominant-class

    # Up-class precision/recall/F1.
    tp = int(np.sum((pred > 0) & (label > 0)))
    fp = int(np.sum((pred > 0) & (label < 0)))
    fn = int(np.sum((pred < 0) & (label > 0)))
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "n": n,
        "accuracy": acc,
        "buy_hold_acc": up_rate,
        "majority_acc": majority,
        "lift_vs_majority": acc - majority,   # >0 means it beats the dumb baseline
        "precision_up": precision,
        "recall_up": recall,
        "f1_up": f1,
    }


def equity_curve(pred: np.ndarray, ret: np.ndarray, cost: float) -> dict:
    """Illustrative long/short equity with per-position-change cost.

    NOT a trading sim — costs/slippage are crude. It exists to show how a tiny
    directional edge survives (or doesn't survive) frictions.
    """
    mask = np.isfinite(ret) & np.isfinite(pred)
    pred, ret = pred[mask], ret[mask]
    if len(ret) == 0:
        return {"total_return": 0.0, "sharpe": 0.0, "curve": np.array([1.0])}

    position = pred
    turnover = np.abs(np.diff(np.concatenate([[0], position])))
    strat_ret = position * ret - turnover * cost
    curve = np.cumprod(1 + strat_ret)
    sharpe = float(np.mean(strat_ret) / np.std(strat_ret) * np.sqrt(252)) if np.std(strat_ret) else 0.0
    return {"total_return": float(curve[-1] - 1), "sharpe": sharpe, "curve": curve}


def aggregate(rows: list[dict]) -> dict:
    """Pool per-fold/per-stock counts into one weighted summary."""
    rows = [r for r in rows if r.get("n")]
    if not rows:
        return {"n": 0}
    n = sum(r["n"] for r in rows)
    w = lambda key: sum(r[key] * r["n"] for r in rows) / n  # noqa: E731
    return {
        "n": n,
        "accuracy": w("accuracy"),
        "buy_hold_acc": w("buy_hold_acc"),
        "majority_acc": w("majority_acc"),
        "lift_vs_majority": w("lift_vs_majority"),
        "precision_up": w("precision_up"),
        "recall_up": w("recall_up"),
        "f1_up": w("f1_up"),
    }
