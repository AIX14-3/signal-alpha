"""Pre-registered grid for the DataLab feature×label sweep.

The point of a *pre-registered* grid (enumerated in code before any run) is
honesty: the sweep engine (``app.ml.sweep``) then corrects for the WHOLE set of
configs it tries with a sweep-wide BH-FDR, so a lucky cell can't be cherry-picked
after the fact. This module owns the four axes of that grid:

  * **dataset specs**  — how to materialise one ``Panel`` (synthetic / demo / db).
  * **feature families** — named subsets of the built feature matrix (attention
    level, search momentum, spike, activity) so we can ask "which family carries
    the signal", not just "all features at once".
  * **labels**         — direction (sign of forward excess return) at several
    horizons + neutral bands; magnitude is a separate task the same engine runs.
  * **transforms / models** — raw vs within-firm-demeaned features; linear+
    regularised models first (trees as a control, per the ml-features guide).

A ``Cell`` is one point of the cartesian product and hashes to a stable ``key``
so the sweep ledger can skip already-run cells (resumability).

No credentials are needed for the ``synthetic`` and ``demo`` sources — they build
the real feature pipeline on generated rows — so the whole engine is buildable
and testable offline. The ``db`` source raises :class:`GateNeeded` when
``DATABASE_URL`` is absent, which the engine records as a GATE (user must supply
data) rather than a crash.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
from dataclasses import dataclass, field
from datetime import date

import numpy as np


class GateNeeded(Exception):
    """A dataset spec cannot run without user-supplied data/credentials.

    The engine turns this into a GATE ScoreCard (status="gate") so an offline
    sweep reports exactly what a human must unblock (e.g. DATABASE_URL, CSVs)
    instead of dying.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Panel:
    """A uniform (features, label, return, date, firm) table for one dataset spec.

    ``stock_ids`` is ``None`` for synthetic data (no firm identity survives the
    neutral-band filter), which just means within-firm IC is skipped for that
    cell; the demo/db pipelines carry real firm ids.
    """

    X: np.ndarray
    y: np.ndarray
    excess_returns: np.ndarray
    dates: np.ndarray
    stock_ids: np.ndarray | None
    feature_names: list[str]
    task: str  # "direction" | "magnitude"


# --------------------------------------------------------------------------- #
# Dataset specs — each returns a Panel. Keyed by a short source name.          #
# --------------------------------------------------------------------------- #


def _panel_synthetic(*, n_stocks: int, n_dates: int, noise: float, band: float,
                     seed: int, **_) -> Panel:
    from .synthetic import make_synthetic

    d = make_synthetic(
        n_stocks=n_stocks, n_dates=n_dates, noise_scale=noise,
        neutral_band_pct=band, seed=seed,
    )
    return Panel(d.X, d.y, d.excess_returns, d.dates, None, list(d.feature_names),
                 "direction")


def _panel_demo(*, n_stocks: int, weeks: int, horizon: int, band: float,
                signal_step: int, seed: int, **_) -> Panel:
    """Real DataLab feature pipeline (compute_indicators) on generated rows."""
    from .datalab_dataset import build_dataset
    from .datalab_demo import generate_demo

    rows, prices, signal_dates, benchmark = generate_demo(
        n_stocks=n_stocks, weeks=weeks, seed=seed, signal_step=signal_step,
    )
    ds = build_dataset(
        datalab_rows_by_stock=rows,
        prices_by_stock=prices,
        signal_dates_by_stock=signal_dates,
        benchmark=benchmark,
        horizon_sessions=horizon,
        neutral_band_pct=band,
    )
    return Panel(ds.X, ds.y, ds.excess_returns, ds.dates, ds.stock_ids,
                 list(ds.feature_names), "direction")


def _panel_db(*, tickers: tuple[str, ...], start: str, end: str, benchmark: str,
              horizon: int, band: float, signal_step: int, prices_csv: str | None,
              **_) -> Panel:
    """Live DataLab-DB pipeline — GATES when DATABASE_URL / data are absent."""
    if not os.environ.get("DATABASE_URL"):
        raise GateNeeded("DATABASE_URL unset — supply Supabase DSN (or --prices-csv + loaded DataLab/OHLCV)")
    import asyncio

    from .datalab_db import load_from_env

    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass
    database_url = os.environ["DATABASE_URL"]
    ds = asyncio.run(
        load_from_env(
            database_url=database_url,
            tickers=list(tickers),
            start=date.fromisoformat(start),
            end=date.fromisoformat(end),
            benchmark_ticker=benchmark,
            prices_csv=prices_csv,
            horizon_sessions=horizon,
            neutral_band_pct=band,
            signal_step=signal_step,
        )
    )
    if len(ds) == 0:
        raise GateNeeded(
            f"no samples for {list(tickers)} in [{start},{end}] — is DataLab+OHLCV loaded?"
        )
    return Panel(ds.X, ds.y, ds.excess_returns, ds.dates, ds.stock_ids,
                 list(ds.feature_names), "direction")


DATASET_SPECS = {
    "synthetic": _panel_synthetic,
    "demo": _panel_demo,
    "db": _panel_db,
}


# --------------------------------------------------------------------------- #
# Feature families — named subsets of the built matrix, selected by substring. #
# --------------------------------------------------------------------------- #

# Substrings that identify each family within a feature name (production DataLab
# indicators are prefixed ``datalab__``). "all" keeps every column.
_FAMILY_TOKENS: dict[str, tuple[str, ...]] = {
    "level": ("weighted_recent_avg", "weighted_prior_avg", "avg_change_pct"),
    "momentum": ("momentum_pct", "risk_momentum_pct"),
    "spike": ("spike_ratio",),
    "activity": ("observations", "prior_observations", "risk_prior_observations",
                 "days_since_latest"),
}

FEATURE_FAMILIES = ("all", *sorted(_FAMILY_TOKENS))


def select_family(feature_names: list[str], family: str) -> list[int]:
    """Column indices of ``feature_names`` that belong to ``family``.

    ``all`` returns every column. A family returns the columns whose name
    contains any of its tokens; an empty result (e.g. a synthetic panel that has
    no DataLab-named columns) tells the engine to skip that cell honestly rather
    than train on zero features.
    """
    if family == "all":
        return list(range(len(feature_names)))
    tokens = _FAMILY_TOKENS.get(family)
    if not tokens:
        raise KeyError(f"unknown feature family: {family!r}")
    return [i for i, n in enumerate(feature_names) if any(t in n for t in tokens)]


# --------------------------------------------------------------------------- #
# Transforms — applied to the selected feature columns.                        #
# --------------------------------------------------------------------------- #


def apply_transform(X: np.ndarray, stock_ids: np.ndarray | None, transform: str) -> np.ndarray:
    """``raw`` = identity; ``within_firm_z`` = per-firm demean + unit variance.

    Within-firm z removes each firm's static cross-sectional trait so the model
    can only exploit TIME-VARYING structure (the 2026-07-01 static-vs-timing
    lesson). With no firm ids (synthetic) it is a no-op.
    """
    if transform == "raw" or stock_ids is None:
        return X
    if transform != "within_firm_z":
        raise KeyError(f"unknown transform: {transform!r}")
    out = X.astype(float).copy()
    for sid in np.unique(stock_ids):
        m = stock_ids == sid
        if m.sum() < 2:
            continue
        block = out[m]
        mean = np.nanmean(block, axis=0)
        std = np.nanstd(block, axis=0)
        std = np.where(std > 0, std, 1.0)
        out[m] = (block - mean) / std
    return out


TRANSFORMS = ("raw", "within_firm_z")


# --------------------------------------------------------------------------- #
# Models — curated subset of the shipping registries (linear-first).           #
# --------------------------------------------------------------------------- #

DIRECTION_MODELS = ("logistic", "ridge", "lda", "hist_grad_boost")
MAGNITUDE_MODELS = ("ridge", "linear", "hist_grad_boost")


def build_model(name: str, task: str, seed: int):
    from .models import build_classifier_registry, build_regressor_registry

    registry = (
        build_regressor_registry(seed=seed)
        if task == "magnitude"
        else build_classifier_registry(seed=seed)
    )
    if name not in registry:
        raise KeyError(f"model {name!r} not in {task} registry")
    return registry[name]


# --------------------------------------------------------------------------- #
# Cell — one point of the grid; hashes to a stable resume key.                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Cell:
    source: str
    universe: str          # human label for the ticker set / demo panel
    label: str             # e.g. "dir_h5_b0.3"
    task: str              # "direction" | "magnitude"
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
            "source": self.source,
            "universe": self.universe,
            "label": self.label,
            "task": self.task,
            "horizon": self.horizon,
            "band": self.band,
            "feature_family": self.feature_family,
            "transform": self.transform,
            "model": self.model,
            "seed": self.seed,
            "extra": list(self.extra),
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


def build_panel_for(cell: Cell) -> Panel:
    spec = DATASET_SPECS.get(cell.source)
    if spec is None:
        raise KeyError(f"unknown source: {cell.source!r}")
    kwargs = dict(cell.extra)
    kwargs.update(horizon=cell.horizon, band=cell.band, seed=cell.seed)
    return spec(**kwargs)


# --------------------------------------------------------------------------- #
# Pre-registered grids.                                                        #
# --------------------------------------------------------------------------- #

# Direction labels: (name, horizon sessions, neutral band pct). Non-overlapping
# outcome windows are enforced downstream via signal_step & embargo.
_DIR_LABELS = {
    "small": [("dir_h5_b0.3", 5, 0.3), ("dir_h20_b0.5", 20, 0.5)],
    "demo": [("dir_h5_b0.3", 5, 0.3), ("dir_h10_b0.4", 10, 0.4),
             ("dir_h20_b0.5", 20, 0.5)],
    "full": [("dir_h1_b0.2", 1, 0.2), ("dir_h5_b0.3", 5, 0.3),
             ("dir_h10_b0.4", 10, 0.4), ("dir_h20_b0.5", 20, 0.5),
             ("dir_h60_b0.8", 60, 0.8)],
}


def build_grid(
    *,
    source: str = "demo",
    size: str = "small",
    universe: str = "demo",
    seed: int = 42,
    extra: dict | None = None,
) -> list[Cell]:
    """Enumerate the pre-registered cartesian product for ``source``/``size``.

    ``extra`` carries source-specific panel knobs (demo: n_stocks/weeks/
    signal_step; db: tickers/start/end/benchmark/prices_csv). Only feature
    families that can be non-empty for the source are used (synthetic → "all").
    """
    labels = _DIR_LABELS.get(size)
    if labels is None:
        raise KeyError(f"unknown grid size: {size!r}")

    if source == "synthetic":
        families = ["all"]
        transforms = ["raw"]
        models = ["logistic", "ridge"]
    elif size == "small":
        families = ["all", "level", "momentum"]
        transforms = ["raw", "within_firm_z"]
        models = ["logistic", "ridge"]
    else:
        families = list(FEATURE_FAMILIES)
        transforms = list(TRANSFORMS)
        models = list(DIRECTION_MODELS)

    extra_items = tuple(sorted((extra or {}).items()))
    cells: list[Cell] = []
    for (lname, horizon, band), family, transform, model in itertools.product(
        labels, families, transforms, models
    ):
        cells.append(
            Cell(
                source=source, universe=universe, label=lname, task="direction",
                horizon=horizon, band=band, feature_family=family,
                transform=transform, model=model, seed=seed, extra=extra_items,
            )
        )
    return cells


__all__ = [
    "GateNeeded", "Panel", "Cell", "DATASET_SPECS", "FEATURE_FAMILIES",
    "TRANSFORMS", "DIRECTION_MODELS", "MAGNITUDE_MODELS",
    "select_family", "apply_transform", "build_model", "build_panel_for",
    "build_grid",
]
