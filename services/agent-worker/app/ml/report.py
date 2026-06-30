"""Render the bake-off comparison table.

One row per model, columns grouped by the four metric families. Every model is
shown next to the majority baseline's accuracy as a delta (``acc_vs_base``) — the
single most important honesty check: a model that doesn't clear the baseline has
learned nothing, no matter how good its raw accuracy looks. Rows are sorted by
Rank-IC (the most robust "does the score track returns" measure).
"""

from __future__ import annotations

import csv
import math
from io import StringIO

from .evaluation import ModelReport

_BASELINE_KEY = "baseline_majority"  # classification (direction) baseline

_COLUMNS = [
    ("model", "model", 22),
    ("n", "n_test", 6),
    ("acc", "accuracy_mean", 7),
    ("sd_acc", "accuracy_std", 7),
    ("Dbase", "_acc_delta", 7),
    ("f1", "f1_mean", 7),
    ("roc_auc", "roc_auc_mean", 8),
    ("IC", "ic_mean", 7),
    ("sd_IC", "ic_std", 7),
    ("rankIC", "rank_ic_mean", 8),
    ("dec_sprd", "decile_spread_mean", 9),
]

# Magnitude (regression) table: rank by predicted-vs-realized magnitude skill.
# R2 is the honest "beats the mean baseline" check (R2>0 ⟺ better than the
# DummyRegressor mean), so a separate baseline-delta column would be redundant —
# the mean baseline itself has an undefined Rank-IC (its prediction is constant).
_REG_COLUMNS = [
    ("model", "model", 22),
    ("n", "n_test", 6),
    ("rankIC", "rank_ic_mean", 8),
    ("sd_rIC", "rank_ic_std", 8),
    ("IC", "ic_mean", 7),
    ("R2", "r2_mean", 7),
    ("MAE", "mae_mean", 9),
    ("RMSE", "rmse_mean", 9),
    ("dec_sprd", "decile_spread_mean", 9),
]


def _rows(reports: list[ModelReport], task: str = "direction") -> list[dict[str, float]]:
    summaries = {r.name: r.summary() for r in reports}
    base_acc = summaries.get(_BASELINE_KEY, {}).get("accuracy_mean", math.nan)
    rows = []
    for name, s in summaries.items():
        s = dict(s)
        s["model"] = name
        s["_acc_delta"] = s["accuracy_mean"] - base_acc
        rows.append(s)
    # Sort by Rank-IC desc, keeping baselines visible at the bottom on ties.
    rows.sort(
        key=lambda r: (
            -1e9 if r["model"].startswith("baseline") else _nan_to_neg(r["rank_ic_mean"])
        ),
        reverse=True,
    )
    return rows


def _nan_to_neg(x: float) -> float:
    return -1e9 if (x is None or math.isnan(x)) else x


def _fmt(value) -> str:
    if isinstance(value, str):
        return value
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    if isinstance(value, int):
        return str(value)
    return f"{value:+.3f}" if abs(value) < 100 else f"{value:.1f}"


def render_table(reports: list[ModelReport], task: str = "direction") -> str:
    """Human-readable, fixed-width comparison table as a string."""
    rows = _rows(reports, task=task)
    columns = _REG_COLUMNS if task == "magnitude" else _COLUMNS
    header = "  ".join(f"{title:>{w}}" for title, _, w in columns)
    lines = [header, "  ".join("-" * w for _, _, w in columns)]
    for r in rows:
        cells = []
        for _, key, w in columns:
            raw = r.get(key)
            text = raw if key == "model" else _fmt(raw)
            cells.append(f"{text:>{w}}")
        lines.append("  ".join(cells))
    lines.append("")
    if task == "magnitude":
        lines.append(
            "Read: rankIC>0 means predicted magnitude tracks realized magnitude; "
            "R2>0 means it beats the mean baseline (DummyRegressor); sd_rIC is "
            "fold-to-fold stability (smaller=more reliable)."
        )
    else:
        lines.append(
            "Read: Dbase>0 means it beat the majority baseline; IC/rankIC>0 means the "
            "score tracks future excess return; +/- cols are fold-to-fold stability "
            "(smaller=more reliable)."
        )
    return "\n".join(lines)


def render_csv(reports: list[ModelReport], task: str = "direction") -> str:
    """Full summary as CSV (all metrics, both mean and std), for spreadsheets."""
    rows = _rows(reports, task=task)
    if not rows:
        return ""
    fieldnames = ["model", "_acc_delta"] + [
        k for k in rows[0] if k not in ("model", "_acc_delta")
    ]
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()
