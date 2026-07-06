"""Source-agnostic dataset adapters for the feature×label search engine.

The bake-off (``bakeoff._load_*``) returns a lossy 4-tuple ``(X, y, excess, dates)``
that drops ``stock_ids``/``feature_names`` — but the search engine's within-firm
gate and family selection need both. So each adapter here calls the SAME underlying
``build_*``/``load_from_env`` builders directly and returns a full :class:`Panel`
(the research ``Dataset`` fields + a ``task`` tag).

One uniform scoring contract across every source (the reason no regressor registry
is needed): every builder emits a **binary ``y``** (cross-sectional up/above-median)
and carries the **continuous target in ``excess_returns``** (forward excess return
for direction, YoY revenue growth for revenue, |move|/vol for magnitude). The engine
always trains a classifier and scores rank-IC of its bullish score vs ``excess_returns``.
The ``task`` tag is therefore a *label* (how to read ``excess_returns``), not a switch
between classifier and regressor.

Credential/dependency-gated sources (everything except ``synthetic``/``datalab-demo``)
raise :class:`GateNeeded` when ``DATABASE_URL`` or ``signal_alpha_data_access`` is
absent, which the engine records as a GATE card instead of crashing — so the whole
grid is buildable and testable offline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

from .datalab_dataset import Dataset


class GateNeeded(Exception):
    """A source cannot run without user-supplied data/credentials.

    The engine turns this into a GATE ScoreCard (status="gate") so an offline
    sweep reports exactly what a human must unblock (DATABASE_URL, a revenue CSV,
    a prices CSV) instead of dying mid-grid.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Panel:
    """A uniform (features, binary label, continuous target, date, firm) table.

    ``stock_ids`` is ``None`` for synthetic data (no firm identity), which just
    means the within-firm IC/transform are skipped for that cell. ``task`` labels
    how ``excess_returns`` should be read: ``direction`` (forward excess return),
    ``revenue`` (next-quarter YoY growth) or ``magnitude`` (|move| / realized vol).
    """

    X: np.ndarray
    y: np.ndarray
    excess_returns: np.ndarray
    dates: np.ndarray
    stock_ids: np.ndarray | None
    feature_names: list[str]
    task: str  # "direction" | "revenue" | "magnitude"


def _panel_from_dataset(ds: Dataset, task: str) -> Panel:
    sids = getattr(ds, "stock_ids", None)
    return Panel(
        X=ds.X,
        y=ds.y,
        excess_returns=ds.excess_returns,
        dates=ds.dates,
        stock_ids=sids if sids is not None and len(sids) else None,
        feature_names=list(ds.feature_names),
        task=task,
    )


# --------------------------------------------------------------------------- #
# Credential / async plumbing shared by the DB-gated adapters.                #
# --------------------------------------------------------------------------- #


def _database_url() -> str:
    """Resolve DATABASE_URL (loading a repo-root .env if python-dotenv is present).

    Raises :class:`GateNeeded` — never ``SystemExit`` — so the sweep records a GATE.
    """
    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise GateNeeded("DATABASE_URL unset — supply the Supabase/local DSN in .env")
    return url


def _run(coro):
    """Run an async loader, converting the offline-machine gates into GateNeeded.

    Missing ``signal_alpha_data_access``/``asyncpg`` (this machine) or a missing
    CSV surface as a clean GATE, not a stack trace.
    """
    import asyncio

    try:
        return asyncio.run(coro)
    except ModuleNotFoundError as e:  # data-access / asyncpg not installed here
        raise GateNeeded(f"dependency missing: {e.name} (real-data run needs data-access + asyncpg)")
    except FileNotFoundError as e:
        raise GateNeeded(f"file missing: {e.filename or e}")


def _tickers(knobs: dict) -> list[str]:
    raw = knobs.get("tickers", "")
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = [t.strip() for t in str(raw).split(",")]
    out = [t for t in items if t]
    if not out:
        raise GateNeeded("no tickers — pass --tickers or a --tickers-file")
    return out


def _require(knobs: dict, name: str, flag: str) -> str:
    val = knobs.get(name)
    if not val:
        raise GateNeeded(f"{flag} required for this source")
    return val


def _iso(knobs: dict, key: str, default: str):
    from datetime import date

    return date.fromisoformat(str(knobs.get(key, default)))


# --------------------------------------------------------------------------- #
# Offline adapters — no credentials (build the real pipeline on generated rows).#
# --------------------------------------------------------------------------- #


def _panel_synthetic(*, seed: int, band: float = 0.3, **knobs) -> Panel:
    from .synthetic import make_synthetic

    d = make_synthetic(
        n_stocks=int(knobs.get("n_stocks", 60)),
        n_dates=int(knobs.get("n_dates", 60)),
        noise_scale=float(knobs.get("noise", 1.0)),
        seed=seed,
    )
    return Panel(d.X, d.y, d.excess_returns, d.dates, None, list(d.feature_names),
                 "direction")


def _panel_datalab_demo(*, seed: int, horizon: int, band: float, **knobs) -> Panel:
    from .datalab_dataset import build_dataset
    from .datalab_demo import generate_demo

    rows, prices, signal_dates, benchmark = generate_demo(
        n_stocks=int(knobs.get("n_stocks", 8)),
        weeks=int(knobs.get("weeks", 120)),
        seed=seed,
        signal_step=int(knobs.get("signal_step", 5)),
    )
    ds = build_dataset(
        datalab_rows_by_stock=rows,
        prices_by_stock=prices,
        signal_dates_by_stock=signal_dates,
        benchmark=benchmark,
        lookback_days=int(knobs.get("lookback", 30)),
        horizon_sessions=horizon,
        neutral_band_pct=band,
    )
    return _panel_from_dataset(ds, "direction")


# --------------------------------------------------------------------------- #
# DB-gated direction adapters.                                                 #
# --------------------------------------------------------------------------- #


def _db_common(knobs: dict) -> dict:
    return dict(
        database_url=_database_url(),
        tickers=_tickers(knobs),
        start=_iso(knobs, "start", "2021-01-01"),
        end=_iso(knobs, "end", "2023-12-31"),
        benchmark_ticker=knobs.get("benchmark") or None,
        prices_csv=knobs.get("prices_csv") or None,
    )


def _panel_datalab(*, seed: int, horizon: int, band: float, **knobs) -> Panel:
    from .datalab_db import load_from_env

    ds = _run(load_from_env(
        **_db_common(knobs),
        lookback_days=int(knobs.get("lookback", 30)),
        horizon_sessions=horizon,
        neutral_band_pct=band,
        signal_step=int(knobs.get("signal_step", 20)),
    ))
    _nonempty(ds, "datalab")
    return _panel_from_dataset(ds, "direction")


def _panel_hiring(*, seed: int, horizon: int, band: float, **knobs) -> Panel:
    from .hiring_db import load_from_env

    fs = knobs.get("feature_set", "volume")
    if fs not in ("volume", "duty", "volume+duty"):
        fs = "volume"
    target = knobs.get("target", "direction")
    ds = _run(load_from_env(
        **_db_common(knobs),
        lookback_days=int(knobs.get("lookback", 90)),
        horizon_sessions=horizon,
        neutral_band_pct=band,
        signal_step=int(knobs.get("signal_step", 20)),
        min_observations=int(knobs.get("min_obs", 2)),
        precise_rematch=bool(knobs.get("precise_rematch", False)),
        feature_set=fs,
        target=target,
        min_cross_section=int(knobs.get("min_cross_section", 6)),
    ))
    _nonempty(ds, "hiring")
    return _panel_from_dataset(ds, "magnitude" if target != "direction" else "direction")


def _panel_patent(*, seed: int, horizon: int, band: float, **knobs) -> Panel:
    from .patent_db import load_from_env

    _COUNT_ONLY_DROP = frozenset({
        "patent__llm_enriched_count", "patent__mean_significance",
        "patent__max_significance", "patent__new_category_count",
    })
    exclude = _COUNT_ONLY_DROP if knobs.get("feature_set") == "count" else frozenset()
    target = knobs.get("target", "direction")
    ds = _run(load_from_env(
        **_db_common(knobs),
        lookback_days=int(knobs.get("lookback", 90)),
        horizon_sessions=horizon,
        neutral_band_pct=band,
        signal_step=int(knobs.get("signal_step", 20)),
        xs_normalize=knobs.get("xs_normalize", "none"),
        exclude_features=exclude,
        target=target,
        min_cross_section=int(knobs.get("min_cross_section", 6)),
    ))
    _nonempty(ds, "patent")
    return _panel_from_dataset(ds, "magnitude" if target != "direction" else "direction")


def _panel_fusion(*, seed: int, horizon: int, band: float, **knobs) -> Panel:
    from .fusion_db import load_fusion

    sources = [s.strip() for s in str(knobs.get("fusion_sources", "patent,hiring,datalab")).split(",") if s.strip()]
    ds = _run(load_fusion(
        **_db_common(knobs),
        sources=sources,
        lookback_days=int(knobs.get("lookback", 60)),
        horizon_sessions=horizon,
        neutral_band_pct=band,
        signal_step=int(knobs.get("signal_step", 20)),
        xs_normalize=knobs.get("xs_normalize", "rank"),
        min_sources=knobs.get("fusion_min_sources"),
    ))
    _nonempty(ds, "fusion")
    return _panel_from_dataset(ds, "direction")


# --------------------------------------------------------------------------- #
# DB-gated revenue-nowcast adapters (the validated edge).                      #
# --------------------------------------------------------------------------- #


def _panel_revenue(*, seed: int, **knobs) -> Panel:
    from .fundamentals_dataset import load_from_env

    fs = knobs.get("feature_set", "volume")
    if fs not in ("volume", "duty", "volume+duty"):
        fs = "volume"
    ds = _run(load_from_env(
        database_url=_database_url(),
        revenue_csv=_require(knobs, "revenue_csv", "--revenue-csv"),
        tickers=_tickers(knobs),
        lookback_days=int(knobs.get("lookback", 90)),
        feature_set=fs,
        min_observations=int(knobs.get("min_obs", 2)),
        min_cross_section=int(knobs.get("min_cross_section", 6)),
        precise_rematch=bool(knobs.get("precise_rematch", True)),
    ))
    _nonempty(ds, "revenue")
    return _panel_from_dataset(ds, "revenue")


def _panel_revenue_offline(*, seed: int, **knobs) -> Panel:
    """오프라인 채용→매출 나우캐스트 — MCP 덤프 + revenue CSV (``load_from_env`` 대체).

    이 머신엔 prod ``DATABASE_URL`` 이 없어 DB 어댑터(:func:`_panel_revenue`)가 GATE 되므로,
    ``within_firm_hiring_revenue_offline`` 과 **동일한** ``build_dataset_from_dumps`` 경로로
    (Supabase MCP 로 뽑은 stocks/HIRING 덤프 + OpenDART revenue CSV) → confirmed 런과 동형의
    Dataset 을 만든다. 덤프 경로·유니버스·피처셋은 ``extra`` knob 으로 받는다. 파일 부재는
    :class:`GateNeeded` 로 안내(크래시 없음).
    """
    import json

    from .hiring_mcp_offline import build_dataset_from_dumps

    stocks_json = _require(knobs, "stocks_json", "--stocks-json")
    postings_jsonl = _require(knobs, "postings_jsonl", "--postings-jsonl")
    revenue_csv = _require(knobs, "revenue_csv", "--revenue-csv")
    try:
        stocks_rows = [tuple(r) for r in json.load(open(stocks_json, encoding="utf-8"))]
        postings = [json.loads(ln) for ln in open(postings_jsonl, encoding="utf-8") if ln.strip()]
    except FileNotFoundError as e:
        raise GateNeeded(f"file missing: {e.filename or e}")
    fs = knobs.get("feature_set", "volume+duty")
    if fs not in ("volume", "duty", "volume+duty"):
        fs = "volume+duty"
    ds = build_dataset_from_dumps(
        stocks_rows=stocks_rows, postings=postings, revenue_csv=revenue_csv,
        tickers=_tickers(knobs), feature_set=fs,
        lookback_days=int(knobs.get("lookback", 90)),
        min_observations=int(knobs.get("min_obs", 2)),
        min_cross_section=int(knobs.get("min_cross_section", 6)),
        signal_step_days=int(knobs.get("signal_step_days", 0)),
    )
    _nonempty(ds, "revenue-offline")
    return _panel_from_dataset(ds, "revenue")


def _panel_patent_revenue(*, seed: int, **knobs) -> Panel:
    from .fundamentals_dataset import load_patent_revenue_from_env

    ds = _run(load_patent_revenue_from_env(
        database_url=_database_url(),
        revenue_csv=_require(knobs, "revenue_csv", "--revenue-csv"),
        tickers=_tickers(knobs),
        lookback_days=int(knobs.get("lookback", 365)),
        min_observations=int(knobs.get("min_obs", 1)),
        min_cross_section=int(knobs.get("min_cross_section", 6)),
        xs_normalize=knobs.get("xs_normalize", "none"),
    ))
    _nonempty(ds, "patent-revenue")
    return _panel_from_dataset(ds, "revenue")


def _panel_fusion_revenue(*, seed: int, **knobs) -> Panel:
    """特허+채용 피처를 (종목,분기)로 조인 → 차기분기 매출성장(신규 융합-매출 경로).

    Reuses the two shipped revenue builders (hiring→rev, patent→rev), inner-joins
    their surviving rows on (stock_id, quarter-end), concatenates feature blocks,
    and RE-binarizes ``y`` on the joined cross-section (each builder binarized on its
    own surviving set; the joined median is the honest label for the fused panel).
    ``excess_returns`` — the raw YoY growth — is identical for a shared key, so the
    join label is unambiguous.
    """
    from .fundamentals_dataset import (
        load_from_env,
        load_patent_revenue_from_env,
    )

    db = _database_url()
    rev_csv = _require(knobs, "revenue_csv", "--revenue-csv")
    tickers = _tickers(knobs)
    mcs = int(knobs.get("min_cross_section", 6))
    fs = knobs.get("feature_set", "volume+duty")
    if fs not in ("volume", "duty", "volume+duty"):
        fs = "volume+duty"

    ds_h = _run(load_from_env(
        database_url=db, revenue_csv=rev_csv, tickers=tickers,
        lookback_days=int(knobs.get("lookback", 90)), feature_set=fs,
        min_observations=int(knobs.get("min_obs", 2)), min_cross_section=mcs,
        precise_rematch=bool(knobs.get("precise_rematch", True)),
    ))
    ds_p = _run(load_patent_revenue_from_env(
        database_url=db, revenue_csv=rev_csv, tickers=tickers,
        lookback_days=int(knobs.get("patent_lookback", 365)),
        min_observations=int(knobs.get("patent_min_obs", 1)), min_cross_section=mcs,
        xs_normalize=knobs.get("xs_normalize", "none"),
    ))
    return _join_revenue_panels(ds_h, ds_p, min_cross_section=mcs)


def _join_revenue_panels(ds_h: Dataset, ds_p: Dataset, *, min_cross_section: int) -> Panel:
    from .magnitude import cross_sectional_median_labels

    def _index(ds: Dataset) -> dict[tuple[int, int], int]:
        return {(int(s), int(d)): i for i, (s, d) in enumerate(zip(ds.stock_ids, ds.dates))}

    hi, pi = _index(ds_h), _index(ds_p)
    keys = sorted(set(hi) & set(pi))
    if not keys:
        raise GateNeeded("fusion-revenue: no shared (stock,quarter) between hiring & patent revenue rows")
    names = [f"h::{n}" for n in ds_h.feature_names] + [f"p::{n}" for n in ds_p.feature_names]
    rows, growths, qdates, sids = [], [], [], []
    for (sid, qd) in keys:
        h, p = hi[(sid, qd)], pi[(sid, qd)]
        rows.append(np.concatenate([ds_h.X[h], ds_p.X[p]]))
        growths.append(float(ds_h.excess_returns[h]))  # == ds_p.excess_returns[p] for a shared key
        qdates.append(qd)
        sids.append(sid)
    keep, y = cross_sectional_median_labels(growths, qdates, min_cross_section=min_cross_section)
    X = np.array([rows[i] for i in keep], dtype=float)
    return Panel(
        X=X.reshape(len(keep), len(names)) if len(keep) else np.empty((0, len(names))),
        y=np.array(y, dtype=int),
        excess_returns=np.array([growths[i] for i in keep], dtype=float),
        dates=np.array([qdates[i] for i in keep], dtype=int),
        stock_ids=np.array([sids[i] for i in keep], dtype=int),
        feature_names=names,
        task="revenue",
    )


def _nonempty(ds: Dataset, source: str) -> None:
    if len(ds) == 0:
        raise GateNeeded(
            f"{source}: 0 samples built — is the data loaded for these tickers/window? "
            f"dropped={dict(ds.dropped)}"
        )


# --------------------------------------------------------------------------- #
# Registry + label tasks.                                                      #
# --------------------------------------------------------------------------- #

DATASET_SPECS = {
    "synthetic": _panel_synthetic,
    "datalab-demo": _panel_datalab_demo,
    "datalab": _panel_datalab,
    "hiring": _panel_hiring,
    "patent": _panel_patent,
    "fusion": _panel_fusion,
    "revenue": _panel_revenue,
    "revenue-offline": _panel_revenue_offline,
    "patent-revenue": _panel_patent_revenue,
    "fusion-revenue": _panel_fusion_revenue,
}

# Which label the source carries (drives report grouping; excess_returns semantics).
SOURCE_TASK = {
    "synthetic": "direction", "datalab-demo": "direction", "datalab": "direction",
    "hiring": "direction", "patent": "direction", "fusion": "direction",
    "revenue": "revenue", "revenue-offline": "revenue",
    "patent-revenue": "revenue", "fusion-revenue": "revenue",
}

# Sources that need no credentials — the offline-testable set.
OFFLINE_SOURCES = ("synthetic", "datalab-demo")


def build_panel_for(source: str, *, horizon: int, band: float, seed: int, extra: dict | None = None) -> Panel:
    spec = DATASET_SPECS.get(source)
    if spec is None:
        raise KeyError(f"unknown source: {source!r}")
    knobs = dict(extra or {})
    return spec(seed=seed, horizon=horizon, band=band, **knobs)


# --------------------------------------------------------------------------- #
# Feature families — named column subsets, disambiguated by source prefix.     #
# --------------------------------------------------------------------------- #

_FAMILY_TOKENS: dict[str, tuple[str, ...]] = {
    # DataLab sub-families (production indicators are ``datalab__``-prefixed).
    "dl_level": ("datalab__weighted_recent_avg", "datalab__weighted_prior_avg",
                 "datalab__avg_change_pct"),
    "dl_momentum": ("datalab__momentum_pct", "datalab__risk_momentum_pct"),
    "dl_spike": ("datalab__spike_ratio",),
    "dl_activity": ("datalab__observations", "datalab__prior_observations",
                    "datalab__risk_prior_observations", "datalab__days_since_latest"),
    # Hiring sub-families.
    "hr_duty": ("tech_share",),
    # Patent sub-families.
    "pt_count": ("patent__total", "patent__observations"),
    "pt_momentum": ("patent__momentum_ratio",),
    "pt_newcat": ("patent__new_category",),
    "pt_significance": ("patent__mean_significance", "patent__max_significance"),
    # Whole-source blocks (for fusion decomposition: which source carries it?).
    "src_datalab": ("datalab__", "dl::", "::datalab"),
    "src_hiring": ("hiring__", "h::"),
    "src_patent": ("patent__", "p::"),
}

FEATURE_FAMILIES = ("all", *sorted(_FAMILY_TOKENS))

# Which families to enumerate per source (empty selections just skip honestly).
_SOURCE_FAMILIES: dict[str, tuple[str, ...]] = {
    "synthetic": ("all",),
    "datalab-demo": ("all", "dl_level", "dl_momentum"),
    "datalab": ("all", "dl_level", "dl_momentum", "dl_spike", "dl_activity"),
    "hiring": ("all", "hr_duty"),
    "patent": ("all", "pt_count", "pt_momentum", "pt_significance"),
    "fusion": ("all", "src_patent", "src_hiring", "src_datalab"),
    "revenue": ("all",),
    "revenue-offline": ("all", "hr_duty"),
    "patent-revenue": ("all",),
    "fusion-revenue": ("all", "src_patent", "src_hiring"),
}


def families_for(source: str, size: str) -> list[str]:
    fams = _SOURCE_FAMILIES.get(source, ("all",))
    return ["all"] if size == "small" and source == "synthetic" else list(fams)


def select_family(feature_names: list[str], family: str) -> list[int]:
    """Column indices of ``feature_names`` belonging to ``family`` (``all`` = every column).

    An empty result (a family whose tokens don't appear in this panel) tells the
    engine to skip the cell honestly rather than train on zero features.
    """
    if family == "all":
        return list(range(len(feature_names)))
    tokens = _FAMILY_TOKENS.get(family)
    if not tokens:
        raise KeyError(f"unknown feature family: {family!r}")
    return [i for i, n in enumerate(feature_names) if any(t in n for t in tokens)]


# --------------------------------------------------------------------------- #
# Transforms + models (classification-only; see module docstring).            #
# --------------------------------------------------------------------------- #


def apply_transform(X: np.ndarray, stock_ids: np.ndarray | None, transform: str) -> np.ndarray:
    """``raw`` = identity; ``within_firm_z`` = per-firm demean + unit variance.

    Within-firm z removes each firm's static cross-sectional trait so the model can
    only exploit TIME-VARYING structure (the 2026-07-01 static-vs-timing lesson).
    With no firm ids (synthetic) it is a no-op.
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


def build_model(name: str, seed: int):
    """Return one classifier from the shipping registry.

    Every research panel is a binary cross-sectional label (up/above-median), scored
    by rank-IC of the bullish score vs the continuous ``excess_returns`` — so a single
    classifier registry covers direction, revenue and magnitude tasks alike.
    """
    from .models import build_classifier_registry

    registry = build_classifier_registry(seed=seed)
    if name not in registry:
        raise KeyError(f"model {name!r} not in classifier registry")
    return registry[name]


__all__ = [
    "GateNeeded", "Panel", "DATASET_SPECS", "SOURCE_TASK", "OFFLINE_SOURCES",
    "FEATURE_FAMILIES", "TRANSFORMS", "build_panel_for", "families_for",
    "select_family", "apply_transform", "build_model",
]
