"""Pre-registered grid for the source-agnostic feature×label search.

A *pre-registered* grid (enumerated in code before any run) is what makes the
automated hunt honest: the engine (:mod:`app.ml.research.search`) corrects for the
WHOLE set of cells with a sweep-wide BH-FDR, so a lucky cell can't be cherry-picked
after the fact. This module owns the grid axes and the stable :class:`Cell` key that
lets the ledger resume.

The label axis is keyed off the source's task (see ``adapters.SOURCE_TASK``):
direction sources enumerate (horizon × neutral-band) labels (the null longshot),
revenue sources a single next-quarter nowcast label (the validated edge). Both feed
the SAME classifier + rank-IC engine, side by side under one BH-FDR — so the honest
comparison is grid-wide.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass

from .adapters import SOURCE_TASK, TRANSFORMS, families_for

# Direction labels: (name, horizon sessions, neutral band pct). Non-overlapping
# outcome windows are enforced downstream via purge/embargo folds.
_DIR_LABELS = {
    "small": [("dir_h5_b0.3", 5, 0.3), ("dir_h20_b0.5", 20, 0.5)],
    "demo": [("dir_h5_b0.3", 5, 0.3), ("dir_h10_b0.4", 10, 0.4),
             ("dir_h20_b0.5", 20, 0.5)],
    "full": [("dir_h1_b0.2", 1, 0.2), ("dir_h5_b0.3", 5, 0.3),
             ("dir_h10_b0.4", 10, 0.4), ("dir_h20_b0.5", 20, 0.5),
             ("dir_h60_b0.8", 60, 0.8)],
}

# Revenue/magnitude labels own their binarization inside the builder, so the grid
# carries a single nominal label; horizon is only used to size the fold embargo
# (quarters are ~90 days apart, so any small embargo prevents overlap).
_REVENUE_LABELS = [("rev_nowcast_q", 1, 0.0)]

# Classifier names (must exist in models.build_classifier_registry). Linear-first
# per the ml-features guide; trees as a control at larger sizes.
_MODELS = {
    "small": ("logistic", "ridge"),
    "demo": ("logistic", "ridge", "lda", "hist_grad_boost", "decision_tree"),
    # decision_tree is the confirmed hiring→revenue winner (rankIC +0.128); include the
    # tree family so the honest sweep can reproduce/refute it under grid-wide BH.
    "full": ("logistic", "ridge", "lda", "hist_grad_boost", "random_forest", "decision_tree"),
}


@dataclass(frozen=True)
class Cell:
    source: str
    universe: str          # human label for the ticker set / demo panel
    label: str             # e.g. "dir_h5_b0.3" or "rev_nowcast_q"
    task: str              # "direction" | "revenue" | "magnitude"
    horizon: int
    band: float
    feature_family: str
    transform: str
    model: str
    seed: int
    # source-specific panel knobs, as a sorted tuple of (k, v) so the Cell stays
    # frozen/hashable and the key is deterministic.
    extra: tuple = ()

    def spec_dict(self) -> dict:
        return {
            "source": self.source, "universe": self.universe, "label": self.label,
            "task": self.task, "horizon": self.horizon, "band": self.band,
            "feature_family": self.feature_family, "transform": self.transform,
            "model": self.model, "seed": self.seed, "extra": list(self.extra),
        }

    def key(self) -> str:
        blob = json.dumps(self.spec_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]

    def panel_key(self) -> str:
        """Identity of the underlying Panel (shared across family/transform/model)."""
        blob = json.dumps(
            {"source": self.source, "universe": self.universe, "task": self.task,
             "horizon": self.horizon, "band": self.band, "seed": self.seed,
             "extra": list(self.extra)},
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]

    def extra_dict(self) -> dict:
        return dict(self.extra)


def _labels_for(source: str, size: str) -> list[tuple[str, int, float]]:
    task = SOURCE_TASK.get(source, "direction")
    if task == "direction":
        return _DIR_LABELS.get(size, _DIR_LABELS["small"])
    return _REVENUE_LABELS


def build_grid(
    *,
    source: str = "datalab-demo",
    size: str = "small",
    universe: str = "demo",
    seed: int = 42,
    extra: dict | None = None,
) -> list[Cell]:
    """Enumerate the pre-registered cartesian product for ``source``/``size``.

    ``extra`` carries source-specific panel knobs (demo: n_stocks/weeks/signal_step;
    db: tickers/start/end/benchmark/prices_csv/revenue_csv/…). Only feature families
    that can be non-empty for the source are enumerated; ``within_firm_z`` is dropped
    for synthetic (no firm ids → no-op duplicate).
    """
    task = SOURCE_TASK.get(source, "direction")
    labels = _labels_for(source, size)
    families = families_for(source, size)
    transforms = ["raw"] if source == "synthetic" else list(TRANSFORMS)
    models = list(_MODELS.get(size, _MODELS["small"]))

    extra_items = tuple(sorted((extra or {}).items()))
    cells: list[Cell] = []
    for (lname, horizon, band), family, transform, model in itertools.product(
        labels, families, transforms, models
    ):
        cells.append(
            Cell(
                source=source, universe=universe, label=lname, task=task,
                horizon=horizon, band=band, feature_family=family,
                transform=transform, model=model, seed=seed, extra=extra_items,
            )
        )
    return cells


__all__ = ["Cell", "build_grid"]
