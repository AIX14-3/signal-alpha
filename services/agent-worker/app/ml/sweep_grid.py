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


def _load_dotenv_once() -> None:
    """Best-effort ``.env`` load from cwd upward (tolerated absent)."""
    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass


def _sweep_database_url() -> str | None:
    """DSN for the live DataLab sweep — a Supabase URL for the research sweep can be
    set WITHOUT clobbering the local/worker ``DATABASE_URL``.

    Prefers ``DATALAB_SWEEP_DATABASE_URL`` (paste the Supabase DSN here), falling
    back to ``DATABASE_URL``. Loads ``.env`` first so either can live in a file.
    """
    _load_dotenv_once()
    return os.environ.get("DATALAB_SWEEP_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _panel_db(*, tickers: tuple[str, ...], start: str, end: str, benchmark: str,
              horizon: int, band: float, signal_step: int, prices_csv: str | None,
              **_) -> Panel:
    """Live DataLab-DB pipeline — GATES when no DSN / data are absent."""
    database_url = _sweep_database_url()
    if not database_url:
        raise GateNeeded(
            "no DSN — set DATALAB_SWEEP_DATABASE_URL (Supabase) or DATABASE_URL "
            "(or --prices-csv + loaded DataLab/OHLCV)"
        )
    import asyncio

    from .datalab_db import load_from_env

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


def _panel_demo_magnitude(*, n_stocks: int, weeks: int, signal_step: int,
                          horizon: int, target: str, seed: int, **_) -> Panel:
    """Real magnitude pipeline (build_magnitude_dataset) on generated search+prices."""
    from .datalab_demo import generate_magnitude_demo
    from .magnitude_dataset import build_magnitude_dataset

    search, prices, signal_dates = generate_magnitude_demo(
        n_stocks=n_stocks, weeks=weeks, seed=seed, signal_step=signal_step,
    )
    ds = build_magnitude_dataset(
        search_by_ticker=search, prices_by_ticker=prices,
        signal_dates_by_ticker=signal_dates, target=target, horizon_sessions=horizon,
    )
    return Panel(ds.X, ds.y, ds.excess_returns, ds.dates, ds.stock_ids,
                 list(ds.feature_names), "magnitude")


def _panel_demo_revenue(*, n_stocks: int, weeks: int, signal_step: int, lag: int,
                        seed: int, planted: bool = True, **_) -> Panel:
    """Real search→revenue-nowcast pipeline on generated search + annual revenue.

    Revenue needs several fiscal years, so ``weeks`` is floored at ~8y regardless
    of the generic demo default; ``planted=False`` builds the true-null control.
    """
    from .datalab_demo import generate_revenue_demo
    from .revenue_dataset import build_revenue_dataset

    search, revenue, trading_days, signal_dates = generate_revenue_demo(
        n_stocks=n_stocks, weeks=max(int(weeks), 416), seed=seed,
        signal_step=signal_step, lag=lag, planted=planted,
    )
    ds = build_revenue_dataset(
        search_by_ticker=search, revenue_by_ticker=revenue,
        trading_days_by_ticker=trading_days, signal_dates_by_ticker=signal_dates,
        lag=lag,
    )
    return Panel(ds.X, ds.y, ds.excess_returns, ds.dates, ds.stock_ids,
                 list(ds.feature_names), "revenue")


def _panel_csv_magnitude(*, keyword_csv: str, prices_csv: str, start: str, end: str,
                         horizon: int, target: str, signal_step: int, **_) -> Panel:
    """Real magnitude from CSVs (name-search + close/volume) — no DB, no keys."""
    from datetime import date as _date

    from .magnitude_dataset import load_magnitude_dataset

    ds = load_magnitude_dataset(
        keyword_csv=keyword_csv, prices_csv=prices_csv,
        start=_date.fromisoformat(start), end=_date.fromisoformat(end),
        target=target, horizon_sessions=horizon, signal_step=signal_step,
    )
    if len(ds) == 0:
        raise GateNeeded(
            f"no magnitude samples — check {keyword_csv} (ticker,keyword,period,ratio) "
            f"& {prices_csv} (ticker,date,close,volume) overlap in [{start},{end}]"
        )
    return Panel(ds.X, ds.y, ds.excess_returns, ds.dates, ds.stock_ids,
                 list(ds.feature_names), "magnitude")


def _panel_csv_revenue(*, keyword_csv: str, prices_csv: str, revenue_csv: str,
                       start: str, end: str, lag: int, signal_step: int, **_) -> Panel:
    """Real search→revenue-nowcast from CSVs (name-search + prices + DART revenue)."""
    from datetime import date as _date

    from .revenue_dataset import load_revenue_dataset

    ds = load_revenue_dataset(
        keyword_csv=keyword_csv, prices_csv=prices_csv, revenue_csv=revenue_csv,
        start=_date.fromisoformat(start), end=_date.fromisoformat(end),
        lag=lag, signal_step=signal_step,
    )
    if len(ds) == 0:
        raise GateNeeded(
            f"no revenue samples — check {revenue_csv} (ticker,name,year,reprt,account,"
            f"fs_div,amount) + search/price overlap in [{start},{end}]"
        )
    return Panel(ds.X, ds.y, ds.excess_returns, ds.dates, ds.stock_ids,
                 list(ds.feature_names), "revenue")


def _panel_db_magnitude(*, prices_csv: str | None = None, **_) -> Panel:
    """Live search→magnitude — GATES (offline scope); names exactly what's needed."""
    if not _sweep_database_url():
        raise GateNeeded(
            "no DSN — set DATALAB_SWEEP_DATABASE_URL (Supabase) or DATABASE_URL; "
            "search→magnitude db run also needs OHLCV(close,volume) + name-search DataLab"
        )
    raise GateNeeded(
        "db magnitude run not wired offline — supply name-search + OHLCV(volume); "
        "the loop is validated via --source demo --task magnitude"
    )


def _panel_db_revenue(*, prices_csv: str | None = None, **_) -> Panel:
    """Live search→revenue-nowcast — GATES (offline scope); names exactly what's needed."""
    if not _sweep_database_url():
        raise GateNeeded(
            "no DSN — set DATALAB_SWEEP_DATABASE_URL (Supabase) or DATABASE_URL; "
            "search→revenue db run also needs DataLab search + dart_krx250.csv "
            "(ticker,year,reprt,account,amount)"
        )
    raise GateNeeded(
        "db revenue run not wired offline — supply dart_krx250.csv + DataLab search; "
        "the loop is validated via --source demo --task revenue"
    )


DATASET_SPECS = {
    "synthetic": _panel_synthetic,
    "demo": _panel_demo,
    "db": _panel_db,
}

# Task-aware panel registry: (source, task) → spec. Falls back to DATASET_SPECS
# (direction) when a (source, task) pair isn't overridden.
_TASK_SPECS = {
    ("demo", "magnitude"): _panel_demo_magnitude,
    ("db", "magnitude"): _panel_db_magnitude,
    ("csv", "magnitude"): _panel_csv_magnitude,
    ("demo", "revenue"): _panel_demo_revenue,
    ("db", "revenue"): _panel_db_revenue,
    ("csv", "revenue"): _panel_csv_revenue,
}


# --------------------------------------------------------------------------- #
# Feature families — named subsets of the built matrix, selected by substring. #
# --------------------------------------------------------------------------- #

# Substrings that identify each family within a feature name. The DIRECTION path
# uses production DataLab indicators (prefixed ``datalab__``); the MAGNITUDE and
# REVENUE paths use the validated attention feature set (prefixed ``magnitude__``
# / ``search__``): abn, abn_mom, search_level, obs_age. Tokens for both coexist —
# a given panel only carries one prefix, so cross-task tokens simply don't match.
# "all" keeps every column.
_FAMILY_TOKENS: dict[str, tuple[str, ...]] = {
    "level": ("weighted_recent_avg", "weighted_prior_avg", "avg_change_pct",
              "magnitude__search_level", "search__search_level"),
    "momentum": ("momentum_pct", "risk_momentum_pct",
                 "magnitude__abn_mom", "search__abn_mom"),
    "spike": ("spike_ratio",),
    "activity": ("observations", "prior_observations", "risk_prior_observations",
                 "days_since_latest"),
    # search-attention families (magnitude/revenue): abn token also matches abn_mom.
    "attention": ("magnitude__abn", "search__abn"),
    "recency": ("magnitude__obs_age", "search__obs_age"),
}

# Direction keeps its original 5 families; the search tasks use the attention set.
DIRECTION_FAMILIES = ("all", "activity", "level", "momentum", "spike")
SEARCH_FAMILIES = ("all", "attention", "momentum", "level", "recency")
FEATURE_FAMILIES = DIRECTION_FAMILIES  # back-compat export


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
        if task in ("magnitude", "revenue")  # both continuous targets → regression
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
    spec = _TASK_SPECS.get((cell.source, cell.task)) or DATASET_SPECS.get(cell.source)
    if spec is None:
        raise KeyError(f"no panel spec for source={cell.source!r} task={cell.task!r}")
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

# Magnitude labels: (name, forward horizon sessions, target). The band is unused
# for a continuous target (kept 0.0); ``target`` rides in the cell's ``extra`` so
# it is part of the panel identity.
_MAG_LABELS = {
    "small": [("mag_vol_h5", 5, "volatility"), ("mag_vol_h20", 20, "volatility")],
    "demo": [("mag_vol_h5", 5, "volatility"), ("mag_vol_h20", 20, "volatility"),
             ("mag_volume_h20", 20, "volume")],
    "full": [("mag_vol_h1", 1, "volatility"), ("mag_vol_h5", 5, "volatility"),
             ("mag_vol_h20", 20, "volatility"), ("mag_volume_h5", 5, "volume"),
             ("mag_volume_h20", 20, "volume")],
}

# Revenue-nowcast labels: (name, lag). lag1 = soonest upcoming annual print (the
# 2026-06-30 headline), lag0 = concurrent, lag2 = the one after. ``lag`` rides in
# ``extra``; the horizon field carries a fixed embargo basis (annual label, weekly
# signals → cross-sectional per-date IC is the unit, so a modest embargo suffices).
_REV_EMBARGO_H = 20
_REV_LABELS = {
    "small": [("rev_lag1", 1), ("rev_lag2", 2)],
    "demo": [("rev_lag0", 0), ("rev_lag1", 1), ("rev_lag2", 2)],
    "full": [("rev_lag0", 0), ("rev_lag1", 1), ("rev_lag2", 2)],
}


def _grid_axes(source: str, size: str, task: str) -> tuple[list, list[str], list[str], list[str]]:
    """Return (label_specs, families, transforms, models) for one (source,size,task).

    ``label_specs`` items are ``(name, horizon, band, label_extra)`` — ``label_extra``
    carries the per-cell panel knob (magnitude ``target`` / revenue ``lag``) that
    must enter the cell's ``extra`` (and thus the panel key).
    """
    if task == "direction":
        labels = _DIR_LABELS.get(size)
        if labels is None:
            raise KeyError(f"unknown grid size: {size!r}")
        specs = [(n, h, b, {}) for (n, h, b) in labels]
        if source == "synthetic":
            return specs, ["all"], ["raw"], ["logistic", "ridge"]
        if size == "small":
            return specs, ["all", "level", "momentum"], ["raw", "within_firm_z"], ["logistic", "ridge"]
        return specs, list(DIRECTION_FAMILIES), list(TRANSFORMS), list(DIRECTION_MODELS)

    if source == "synthetic":
        raise ValueError(f"task {task!r} has no synthetic source — use --source demo")

    if task == "magnitude":
        labels = _MAG_LABELS.get(size)
        if labels is None:
            raise KeyError(f"unknown grid size: {size!r}")
        specs = [(n, h, 0.0, {"target": t}) for (n, h, t) in labels]
    elif task == "revenue":
        labels = _REV_LABELS.get(size)
        if labels is None:
            raise KeyError(f"unknown grid size: {size!r}")
        specs = [(n, _REV_EMBARGO_H, 0.0, {"lag": lag}) for (n, lag) in labels]
    else:
        raise KeyError(f"unknown task: {task!r}")

    if size == "small":
        return specs, ["all", "attention", "momentum"], ["raw", "within_firm_z"], ["ridge", "linear"]
    return specs, list(SEARCH_FAMILIES), list(TRANSFORMS), list(MAGNITUDE_MODELS)


def build_grid(
    *,
    source: str = "demo",
    size: str = "small",
    universe: str = "demo",
    seed: int = 42,
    extra: dict | None = None,
    task: str = "direction",
) -> list[Cell]:
    """Enumerate the pre-registered cartesian product for ``source``/``size``/``task``.

    ``extra`` carries source-specific panel knobs (demo: n_stocks/weeks/
    signal_step[/planted]; db: tickers/start/end/benchmark/prices_csv). ``task`` picks
    the label/family/model axes: ``direction`` (sign of forward excess return),
    ``magnitude`` (forward realized vol / abnormal volume), or ``revenue`` (next-print
    revenue-YoY nowcast). The magnitude/revenue tasks are DataLab-search only and
    have no synthetic source.
    """
    label_specs, families, transforms, models = _grid_axes(source, size, task)
    base_extra = dict(extra or {})
    cells: list[Cell] = []
    for (lname, horizon, band, label_extra), family, transform, model in itertools.product(
        label_specs, families, transforms, models
    ):
        merged = {**base_extra, **label_extra}
        extra_items = tuple(sorted(merged.items()))
        cells.append(
            Cell(
                source=source, universe=universe, label=lname, task=task,
                horizon=horizon, band=band, feature_family=family,
                transform=transform, model=model, seed=seed, extra=extra_items,
            )
        )
    return cells


__all__ = [
    "GateNeeded", "Panel", "Cell", "DATASET_SPECS", "FEATURE_FAMILIES",
    "DIRECTION_FAMILIES", "SEARCH_FAMILIES", "TRANSFORMS", "DIRECTION_MODELS",
    "MAGNITUDE_MODELS", "select_family", "apply_transform", "build_model",
    "build_panel_for", "build_grid",
]
