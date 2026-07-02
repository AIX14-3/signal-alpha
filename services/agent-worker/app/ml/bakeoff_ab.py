"""Multi-source FUSION bake-off: method A (homogeneous target) vs B (heterogeneous).

Two 2-stage stacking pipelines are compared on the SAME walk-forward panel:

  Method A (control) — every source's stage-1 base model predicts the common
    20-session forward excess return; a stage-2 meta learner combines those
    same-target predictions. Expected weak: per-source DIRECTION was no-signal in
    prior work, so this is the honest baseline.

  Method B (treatment) — each source's base model predicts its own VALIDATED
    label (price -> forward return; DataLab -> forward annual revenue growth, the
    219-name-robust nowcast); the meta learner fuses the heterogeneous base
    predictions onto the final target (forward return direction).

Rigor (mirrors the user's methodology):
  * purged + embargo walk-forward (gap >= horizon) at BOTH stages -> no leakage;
  * stage-1 base predictions are out-of-sample before they feed stage-2 (nested
    walk-forward), so the meta learner never sees a base pred fit on its own row;
  * permutation p-values (within-date shuffle) + Benjamini-Hochberg across every
    scored config -> small-sample-honest, multiple-testing-controlled;
  * within-firm rank-IC decomposition -> static-trait vs time-varying skill.

Data is self-contained from CSVs (no DB):
  --prices  ticker,date,close,volume        (KRX250 daily closes/volumes)
  --search  ticker,keyword,period,ratio      (name-search DataLab, daily)
  --revenue ticker,name,year,reprt,account,fs_div,amount  (DART annual, for B)

BRIDGE 2 (this revision) — the stage-2 meta is no longer only a linear main-effect
combiner. Milestone 1 showed a linear meta on per-source predictions carries no
DIRECTIONAL signal. The conditional-reversal hypothesis (Eom&Park, KR retail
attention) is that attention does not move direction on its own but FLIPS/AMPLIFIES
price momentum's sign. So we re-score the same base OOS predictions with meta
learners that CAN see interactions — explicit product features for a linear meta
(mom×attention, sign(mom)×attention, mom×liquidity) and native interaction splits
for a gradient-boosted meta (toggled on/off via ``interaction_cst`` as a controlled
contrast). The final label stays DIRECTION (sign of forward excess return); we score
the bullish score's rank-IC vs the realized return. Reported across horizon 20/60,
full vs small-cap (bottom liquidity tercile), with a per-period IC sign-flip
diagnostic to expose regime-conditional reversal. Folds are NON-OVERLAPPING
(signal step == horizon) + purge/embargo, permutation p, BH-FDR across all configs.

Run:
  python -m app.ml.bakeoff_ab --method both \
      --prices prices_krx250.csv --search stockname_daily_krx250.csv \
      --revenue dart_krx250.csv --horizons 20,60 --perm 500
"""

from __future__ import annotations

import argparse
import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from .datalab_dataset import PriceSeries, weekly_signal_dates
from .evaluation import (
    PerPeriodIC,
    _decile_spread,
    _safe_corr,
    benjamini_hochberg,
    oos_predictions,
    per_period_ic,
    permutation_pvalue,
    purged_walk_forward_folds,
    within_firm_ic,
)
from .magnitude_dataset import (
    WIN,
    _ffill_to_trading_days,
    _rolling_z,
    aggregate_search_by_ticker,
)
from .prices_csv import load_prices_volume_csv

from scipy.stats import pearsonr, spearmanr


# --------------------------------------------------------------------------- #
# Panel row
# --------------------------------------------------------------------------- #
@dataclass
class PanelRow:
    ticker: str
    as_of: date
    price_feats: dict[str, float]
    datalab_feats: dict[str, float]
    fwd_return: float  # forward excess return over the horizon (final target)
    revenue_yoy: float  # next-print annual revenue YoY growth (method-B DataLab label)


@dataclass
class Panel:
    rows: list[PanelRow] = field(default_factory=list)
    dropped: Counter = field(default_factory=Counter)


# --------------------------------------------------------------------------- #
# Point-in-time feature builders (trailing only)
# --------------------------------------------------------------------------- #
def _price_features(prices: PriceSeries, as_of: date) -> dict[str, float] | None:
    """Trailing momentum / volatility / volume features known at ``as_of``."""
    i = prices._index.get(as_of)
    if i is None or i < 60:
        return None
    closes = prices.closes
    vols = prices.volumes

    def ret(k: int) -> float:
        base = closes[i - k]
        return (closes[i] / base - 1.0) if base > 0 else math.nan

    logrets = [
        math.log(closes[j] / closes[j - 1])
        for j in range(i - 20 + 1, i + 1)
        if closes[j] > 0 and closes[j - 1] > 0
    ]
    logrets60 = [
        math.log(closes[j] / closes[j - 1])
        for j in range(i - 60 + 1, i + 1)
        if closes[j] > 0 and closes[j - 1] > 0
    ]
    vol20 = statistics.pstdev(logrets) if len(logrets) >= 2 else math.nan
    vol60 = statistics.pstdev(logrets60) if len(logrets60) >= 2 else math.nan
    window_hi = max(closes[i - 60 : i + 1])
    feats = {
        "ret_5": ret(5),
        "ret_10": ret(10),
        "ret_20": ret(20),
        "ret_60": ret(60),
        "vol_20": vol20,
        "vol_ratio": (vol20 / vol60) if (vol60 and vol60 > 0) else math.nan,
        "dist_high_60": (closes[i] / window_hi - 1.0) if window_hi > 0 else math.nan,
    }
    if vols:
        base_v = statistics.mean(vols[max(0, i - 60) : i]) if i > 0 else 0.0
        recent_v = statistics.mean(vols[max(0, i - 5) : i + 1])
        feats["vol_abn"] = (recent_v / base_v) if base_v > 0 else math.nan
    return feats


def _forward_excess(
    prices: PriceSeries, as_of: date, horizon: int, market_ret: float | None
) -> float | None:
    raw = prices.forward_return_pct(as_of, horizon)
    if raw is None:
        return None
    return raw - (market_ret if market_ret is not None else 0.0)


# --------------------------------------------------------------------------- #
# DART annual revenue -> next-print YoY growth label (method B DataLab target)
# --------------------------------------------------------------------------- #
def load_annual_revenue(path: str) -> dict[str, list[tuple[int, float]]]:
    """``{ticker: [(fiscal_year, revenue), ...]}`` ascending, from the DART CSV."""
    import csv

    by_ticker: dict[str, dict[int, float]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("account") or "").strip() != "revenue":
                continue
            if (row.get("reprt") or "").strip() != "FY":
                continue
            ticker = (row.get("ticker") or "").strip()
            try:
                year = int(row["year"])
                amount = float(row["amount"])
            except (KeyError, ValueError, TypeError):
                continue
            # Consolidated (CFS) preferred; keep the larger when duplicated.
            cur = by_ticker.setdefault(ticker, {})
            cur[year] = max(cur.get(year, 0.0), amount)
    return {t: sorted(d.items()) for t, d in by_ticker.items()}


def _filing_date(fiscal_year: int) -> date:
    """PIT proxy: an annual report is public by ~April 1 of the following year."""
    return date(fiscal_year + 1, 4, 1)


def _next_revenue_yoy(rev: list[tuple[int, float]], as_of: date) -> float:
    """YoY growth of the SOONEST annual print that becomes public after ``as_of``.

    The label the search is trying to nowcast: features at ``as_of`` are known, the
    revenue print (and its growth vs the prior year) lands after ``as_of``. nan when
    no upcoming print or no prior-year base exists.
    """
    by_year = dict(rev)
    for year, amount in rev:
        if _filing_date(year) > as_of:
            prev = by_year.get(year - 1)
            if prev and prev > 0 and amount > 0:
                return amount / prev - 1.0
            return math.nan
    return math.nan


# --------------------------------------------------------------------------- #
# Panel assembly
# --------------------------------------------------------------------------- #
def build_panel(
    *,
    prices_by_ticker: dict[str, PriceSeries],
    search_by_ticker: dict[str, list[tuple[date, float]]],
    revenue_by_ticker: dict[str, list[tuple[int, float]]],
    tickers: list[str],
    start: date,
    end: date,
    horizon: int,
    signal_step: int,
    mom_lag: int = 5,
) -> Panel:
    """Assemble per-(ticker, signal-date) rows with both sources' PIT features."""
    panel = Panel()
    # Pre-compute per-date cross-sectional forward return -> market proxy for excess.
    raw_fwd: dict[tuple[str, date], float] = {}
    per_date_rets: dict[date, list[float]] = {}
    signals: dict[str, list[date]] = {}
    for ticker in tickers:
        prices = prices_by_ticker.get(ticker)
        if prices is None:
            panel.dropped["no_price_series"] += 1
            continue
        in_window = [d for d in prices.dates if start <= d <= end]
        sd = weekly_signal_dates(in_window, step=signal_step)
        signals[ticker] = sd
        for as_of in sd:
            r = prices.forward_return_pct(as_of, horizon)
            if r is not None:
                raw_fwd[(ticker, as_of)] = r
                per_date_rets.setdefault(as_of, []).append(r)
    market = {d: statistics.mean(v) for d, v in per_date_rets.items() if v}

    for ticker in tickers:
        prices = prices_by_ticker.get(ticker)
        if prices is None:
            continue
        td = prices.dates
        td_index = {d: i for i, d in enumerate(td)}
        level_by_day = _ffill_to_trading_days(td, search_by_ticker.get(ticker, []))
        abn = _rolling_z(td, level_by_day, win=WIN)
        rev = revenue_by_ticker.get(ticker, [])
        for as_of in signals[ticker]:
            pf = _price_features(prices, as_of)
            if pf is None:
                panel.dropped["no_price_features"] += 1
                continue
            fwd = _forward_excess(prices, as_of, horizon, market.get(as_of))
            if fwd is None:
                panel.dropped["no_forward_return"] += 1
                continue
            # DataLab features (may be absent -> nan-filled, model imputes).
            if as_of in abn:
                i = td_index[as_of]
                lag_day = td[i - mom_lag] if i - mom_lag >= 0 else None
                abn_mom = (
                    abn[as_of] - abn[lag_day]
                    if (lag_day is not None and lag_day in abn)
                    else 0.0
                )
                level, obs_date = level_by_day[as_of]
                df = {
                    "abn": abn[as_of],
                    "abn_mom": abn_mom,
                    "search_level": level,
                    "obs_age": float((as_of - obs_date).days),
                }
            else:
                df = {"abn": math.nan, "abn_mom": math.nan,
                      "search_level": math.nan, "obs_age": math.nan}
                panel.dropped["no_search_abn"] += 1
            panel.rows.append(
                PanelRow(
                    ticker=ticker,
                    as_of=as_of,
                    price_feats=pf,
                    datalab_feats=df,
                    fwd_return=fwd,
                    revenue_yoy=_next_revenue_yoy(rev, as_of),
                )
            )
    return panel


# --------------------------------------------------------------------------- #
# Matrix helpers
# --------------------------------------------------------------------------- #
def _matrix(feat_dicts: list[dict[str, float]]) -> tuple[np.ndarray, list[str]]:
    names = sorted({k for d in feat_dicts for k in d})
    X = np.array(
        [[float(d.get(n, math.nan)) for n in names] for d in feat_dicts], dtype=float
    )
    return X, names


def _base_model(seed: int):
    """Strong-regularized base learner (LightGBM if present, else HistGB)."""
    try:
        from lightgbm import LGBMRegressor

        return LGBMRegressor(
            n_estimators=400,
            num_leaves=15,
            max_depth=4,
            learning_rate=0.03,
            reg_alpha=1.0,
            reg_lambda=1.0,
            min_child_samples=50,
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.8,
            random_state=seed,
            verbose=-1,
        )
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import Pipeline

        return Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("model", HistGradientBoostingRegressor(
                    max_depth=4, learning_rate=0.03, random_state=seed)),
            ]
        )


def _meta_models(seed: int) -> dict:
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    return {
        "meta_ridge": Pipeline(
            [("impute", SimpleImputer(strategy="median")),
             ("scale", StandardScaler()),
             ("model", Ridge())]
        ),
        "meta_gbm": Pipeline(
            [("impute", SimpleImputer(strategy="median")),
             ("model", HistGradientBoostingRegressor(
                 max_depth=3, learning_rate=0.05, random_state=seed))]
        ),
    }


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
@dataclass
class ScoreCard:
    name: str
    n: int
    ic: float
    rank_ic: float
    hit_rate: float
    decile_spread: float
    sharpe: float
    within_firm_ic: float
    perm_p: float
    fdr_survive: bool = False
    ppic: PerPeriodIC | None = None


def _long_short_sharpe(
    scores: np.ndarray, returns: np.ndarray, dates: np.ndarray, periods_per_year: float
) -> float:
    """Sharpe of a per-signal-date top-decile-minus-bottom-decile portfolio."""
    by_date: dict = {}
    for i, d in enumerate(dates):
        by_date.setdefault(d, []).append(i)
    pnl = []
    for idx in by_date.values():
        if len(idx) < 10:
            continue
        idx = np.array(idx)
        s = scores[idx]
        r = returns[idx]
        k = max(1, len(idx) // 10)
        order = np.argsort(s)
        pnl.append(r[order[-k:]].mean() - r[order[:k]].mean())
    if len(pnl) < 3:
        return float("nan")
    pnl = np.array(pnl)
    sd = pnl.std(ddof=1)
    return float(pnl.mean() / sd * math.sqrt(periods_per_year)) if sd > 0 else float("nan")


def _score(
    name: str,
    pred: np.ndarray,
    mask: np.ndarray,
    returns: np.ndarray,
    dates: np.ndarray,
    stock_ids: np.ndarray,
    *,
    periods_per_year: float,
    n_perm: int,
    seed: int,
) -> ScoreCard:
    p = pred[mask]
    r = returns[mask]
    d = dates[mask]
    sid = stock_ids[mask]
    ic = _safe_corr(pearsonr, p, r)
    ric = _safe_corr(spearmanr, p, r)
    hit = float(np.mean((np.sign(p) == np.sign(r))[np.sign(r) != 0])) if len(p) else float("nan")
    ds = _decile_spread(p, r)
    sharpe = _long_short_sharpe(p, r, d, periods_per_year)
    wfic = within_firm_ic(p, r, sid)
    _, perm_p = permutation_pvalue(p, r, d, n_perm=n_perm, seed=seed, metric="rank_ic")
    ppic = per_period_ic(p, r, d)
    return ScoreCard(name, len(p), ic, ric, hit, ds, sharpe, wfic, perm_p, ppic=ppic)


# --------------------------------------------------------------------------- #
# Fusion run
# --------------------------------------------------------------------------- #
def run_method(
    panel: Panel,
    *,
    method: str,
    n_folds: int,
    embargo_days: int,
    seed: int,
    n_perm: int,
    periods_per_year: float,
) -> list[ScoreCard]:
    """Run one fusion method (A or B) end-to-end and return scored configs."""
    rows = panel.rows
    Xp, _ = _matrix([r.price_feats for r in rows])
    Xd, _ = _matrix([r.datalab_feats for r in rows])
    fwd = np.array([r.fwd_return for r in rows], dtype=float)
    rev = np.array([r.revenue_yoy for r in rows], dtype=float)
    dates = np.array([r.as_of.toordinal() for r in rows], dtype=int)
    tickers = sorted({r.ticker for r in rows})
    tcode = {t: i for i, t in enumerate(tickers)}
    sid = np.array([tcode[r.ticker] for r in rows], dtype=int)

    folds = purged_walk_forward_folds(dates, n_folds=n_folds, embargo_days=embargo_days)

    # Stage-1 base targets: A -> both forward return; B -> DataLab predicts revenue YoY.
    price_target = fwd
    datalab_target = fwd if method == "A" else rev

    base_price, mp = oos_predictions(_base_model(seed), Xp, price_target, folds, task="magnitude")
    base_datalab, md = oos_predictions(_base_model(seed + 1), Xd, datalab_target, folds, task="magnitude")

    cards: list[ScoreCard] = []
    # Single-source reference signals (predictions of forward return where target==fwd).
    if method == "A":
        cards.append(_score("price_only", base_price, mp, fwd, dates, sid,
                            periods_per_year=periods_per_year, n_perm=n_perm, seed=seed))
        cards.append(_score("datalab_only", base_datalab, md, fwd, dates, sid,
                            periods_per_year=periods_per_year, n_perm=n_perm, seed=seed))

    # Stage-2 meta: fuse the two OOS base predictions onto the final target (fwd return).
    both = mp & md & np.isfinite(fwd)
    M = np.column_stack([base_price, base_datalab])
    meta_folds = purged_walk_forward_folds(dates[both], n_folds=n_folds, embargo_days=embargo_days)
    Mb = M[both]
    fwdb = fwd[both]

    # Equal-weight fusion (no learned meta) — the honest floor for stacking.
    if method == "A":
        with np.errstate(invalid="ignore"):
            eq = np.nanmean(M, axis=1)
        cards.append(_score("fusion_equal_wt", eq, both, fwd, dates, sid,
                            periods_per_year=periods_per_year, n_perm=n_perm, seed=seed))

    for mname, mmodel in _meta_models(seed).items():
        meta_pred, meta_mask = oos_predictions(mmodel, Mb, fwdb, meta_folds, task="magnitude")
        # Re-embed into full-length arrays for uniform scoring.
        full_pred = np.full(len(rows), np.nan)
        full_mask = np.zeros(len(rows), dtype=bool)
        idx_both = np.where(both)[0]
        full_pred[idx_both[meta_mask]] = meta_pred[meta_mask]
        full_mask[idx_both[meta_mask]] = True
        cards.append(_score(f"{mname}", full_pred, full_mask, fwd, dates, sid,
                            periods_per_year=periods_per_year, n_perm=n_perm, seed=seed))
    return cards


# --------------------------------------------------------------------------- #
# BRIDGE 2 — interaction / conditional-reversal meta (DIRECTION target)
#
# Milestone 1 stacked per-source predictions with a LINEAR main-effect meta and
# found no directional signal. The hypothesis here (Eom&Park-style): attention
# does not move direction on its own but CONDITIONALLY flips/amplifies price
# momentum's sign. A linear main-effect meta is blind to that product term. So we
# re-score the SAME base OOS predictions with meta learners that can see
# interactions — explicit product features for the linear meta, and native
# interaction splits for a gradient-boosted meta (gated on/off via interaction_cst
# so "allow interactions" vs "forbid them" is a controlled contrast).
#
# Direction label is preserved: the stage-2 target is sign(forward excess return);
# we score the bullish score's rank-IC against the realized return, exactly as the
# aggregator's directional objective. Non-overlapping folds (signal_step==horizon)
# + purge/embargo + within-date permutation + BH-FDR are unchanged.
# --------------------------------------------------------------------------- #
def _condition_vars(rows: list[PanelRow]) -> dict[str, np.ndarray]:
    """Row-aligned conditioning variables for the interaction meta.

    * ``mom``  — 20-session price momentum (``ret_20``); the effect being modulated.
    * ``att``  — attention z (search ``abn``); the hypothesised modulator.
    * ``liq``  — abnormal-volume proxy (``vol_abn``) standing in for liquidity.
    * ``sgn``  — sign of trailing momentum (regime indicator for reversal).
    """
    mom = np.array([r.price_feats.get("ret_20", math.nan) for r in rows], dtype=float)
    att = np.array([r.datalab_feats.get("abn", math.nan) for r in rows], dtype=float)
    liq = np.array([r.price_feats.get("vol_abn", math.nan) for r in rows], dtype=float)
    with np.errstate(invalid="ignore"):
        sgn = np.sign(mom)
    return {"mom": mom, "att": att, "liq": liq, "sgn": sgn}


def _meta_matrix(
    base_price: np.ndarray,
    base_datalab: np.ndarray,
    cv: dict[str, np.ndarray],
    *,
    with_conditions: bool,
    with_interactions: bool,
) -> np.ndarray:
    """Stack base OOS predictions with (optionally) condition vars + explicit products.

    ``with_interactions`` adds the hypothesis-driven product terms so even a LINEAR
    meta can express "attention flips momentum": ``mom*att``, ``sgn*att``,
    ``mom*liq``. Trees get those interactions for free, so their configs pass
    ``with_interactions=False`` and toggle interactions via the model's
    ``interaction_cst`` instead.
    """
    cols = [base_price, base_datalab]
    if with_conditions:
        cols += [cv["mom"], cv["att"], cv["liq"], cv["sgn"]]
    if with_interactions:
        cols += [cv["mom"] * cv["att"], cv["sgn"] * cv["att"], cv["mom"] * cv["liq"]]
    return np.column_stack(cols)


def _linear_meta():
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    return Pipeline(
        [("impute", SimpleImputer(strategy="median")),
         ("scale", StandardScaler()),
         ("model", LogisticRegression(max_iter=2000, C=0.5))]
    )


def _gbm_meta(seed: int, *, interactions: bool):
    """Gradient-boosted DIRECTION meta; ``interactions`` toggles interaction_cst.

    ``interactions=False`` forbids cross-feature splits (``no_interactions``) — a
    purely additive tree, the honest contrast for "do interactions add anything?".
    HistGB consumes NaNs natively, so no imputer is needed.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(
        max_depth=3,
        learning_rate=0.05,
        max_iter=200,
        l2_regularization=1.0,
        min_samples_leaf=40,
        random_state=seed,
        interaction_cst=None if interactions else "no_interactions",
    )


# (name, model_factory, feature-spec) for the direction meta bake-off.
def _interaction_configs(seed: int):
    return [
        # linear main-effect meta ≡ Milestone 1 control (base preds only).
        ("ridge_main", _linear_meta,
         dict(with_conditions=False, with_interactions=False)),
        # linear meta + explicit interaction products (attention×momentum etc.).
        ("ridge_ix", _linear_meta,
         dict(with_conditions=True, with_interactions=True)),
        # gradient-boosted meta, interactions FORBIDDEN (additive control).
        ("gbm_main", lambda: _gbm_meta(seed, interactions=False),
         dict(with_conditions=True, with_interactions=False)),
        # gradient-boosted meta, interactions ALLOWED (the treatment).
        ("gbm_ix", lambda: _gbm_meta(seed, interactions=True),
         dict(with_conditions=True, with_interactions=False)),
    ]


def run_interaction_meta(
    panel: Panel,
    *,
    method: str,
    n_folds: int,
    embargo_days: int,
    seed: int,
    n_perm: int,
    periods_per_year: float,
    universes: list[tuple[str, np.ndarray | None]],
) -> list[ScoreCard]:
    """Direction-target interaction bake-off; scores every config on every universe.

    Base stage-1 OOS predictions are computed ONCE (identical across universes and
    meta configs); each meta config's full-length OOS direction score is then scored
    on each ``(tag, row_mask)`` universe (mask ``None`` == full panel). Reusing the
    predictions keeps the small-cap subset a pure re-scoring of the same fused
    signal (the honest "is the effect concentrated in small caps?" test).
    """
    rows = panel.rows
    Xp, _ = _matrix([r.price_feats for r in rows])
    Xd, _ = _matrix([r.datalab_feats for r in rows])
    fwd = np.array([r.fwd_return for r in rows], dtype=float)
    rev = np.array([r.revenue_yoy for r in rows], dtype=float)
    dates = np.array([r.as_of.toordinal() for r in rows], dtype=int)
    tickers = sorted({r.ticker for r in rows})
    tcode = {t: i for i, t in enumerate(tickers)}
    sid = np.array([tcode[r.ticker] for r in rows], dtype=int)
    cv = _condition_vars(rows)

    folds = purged_walk_forward_folds(dates, n_folds=n_folds, embargo_days=embargo_days)
    datalab_target = fwd if method == "A" else rev
    base_price, mp = oos_predictions(_base_model(seed), Xp, fwd, folds, task="magnitude")
    base_datalab, md = oos_predictions(
        _base_model(seed + 1), Xd, datalab_target, folds, task="magnitude"
    )

    both = mp & md & np.isfinite(fwd)
    idx_both = np.where(both)[0]
    ybin = (fwd > 0).astype(int)
    meta_folds = purged_walk_forward_folds(
        dates[both], n_folds=n_folds, embargo_days=embargo_days
    )

    def _emit(name: str, full_pred: np.ndarray, full_mask: np.ndarray) -> list[ScoreCard]:
        out = []
        for tag, row_mask in universes:
            m = full_mask if row_mask is None else (full_mask & row_mask)
            if m.sum() < 10:
                continue
            out.append(
                _score(f"{tag}:{name}", full_pred, m, fwd, dates, sid,
                       periods_per_year=periods_per_year, n_perm=n_perm, seed=seed)
            )
        return out

    cards: list[ScoreCard] = []
    # Equal-weight floor (only meaningful for A: both base preds share the fwd-return
    # scale; for B they are heterogeneous units so a raw average is nonsense).
    if method == "A":
        with np.errstate(invalid="ignore"):
            eq = np.nanmean(np.column_stack([base_price, base_datalab]), axis=1)
        cards.extend(_emit("eqwt", eq, both))

    for name, factory, spec in _interaction_configs(seed):
        Xmeta = _meta_matrix(base_price, base_datalab, cv, **spec)
        task = "direction"
        pred_b, mask_b = oos_predictions(factory(), Xmeta[both], ybin[both], meta_folds, task=task)
        full_pred = np.full(len(rows), np.nan)
        full_mask = np.zeros(len(rows), dtype=bool)
        full_pred[idx_both[mask_b]] = pred_b[mask_b]
        full_mask[idx_both[mask_b]] = True
        cards.extend(_emit(name, full_pred, full_mask))
    return cards


def _smallcap_mask(
    panel: Panel, prices_by_ticker: dict[str, PriceSeries], quantile: float
) -> tuple[np.ndarray, int]:
    """Row mask for the bottom-``quantile`` liquidity tercile (by median volume).

    Liquidity proxy = each ticker's median daily volume over its whole series;
    tickers at/below the ``quantile`` cut are "small/illiquid". Attention effects
    are documented to concentrate there, so we score them as a separate universe.
    """
    med: dict[str, float] = {}
    for t, ps in prices_by_ticker.items():
        if ps.volumes:
            med[t] = float(np.median(ps.volumes))
    if not med:
        return np.zeros(len(panel.rows), dtype=bool), 0
    cut = float(np.quantile(list(med.values()), quantile))
    small = {t for t, v in med.items() if v <= cut}
    mask = np.array([r.ticker in small for r in panel.rows], dtype=bool)
    return mask, len(small)


def _apply_fdr(cards: list[ScoreCard], q: float = 0.10) -> None:
    survive = benjamini_hochberg([c.perm_p for c in cards], q=q)
    for c, s in zip(cards, survive):
        c.fdr_survive = s


def _render(title: str, cards: list[ScoreCard]) -> str:
    lines = [f"\n=== {title} ===",
             f"{'config':<26}{'n':>7}{'IC':>8}{'rankIC':>8}{'hit':>7}"
             f"{'decSpr':>9}{'Sharpe':>8}{'wfIC':>8}{'perm_p':>8}{'FDR':>5}"]
    for c in cards:
        lines.append(
            f"{c.name:<26}{c.n:>7}{c.ic:>8.3f}{c.rank_ic:>8.3f}{c.hit_rate:>7.3f}"
            f"{c.decile_spread:>9.3f}{c.sharpe:>8.2f}{c.within_firm_ic:>8.3f}"
            f"{c.perm_p:>8.3f}{'Y' if c.fdr_survive else '-':>5}"
        )
    return "\n".join(lines)


def _render_ppic(title: str, cards: list[ScoreCard]) -> str:
    """Per-period cross-sectional IC diagnostic: mean/std/t + sign-flip share.

    ``negFrac`` ≈ 0.5 with a tiny mean is the fingerprint of a SIGN-UNSTABLE
    (regime-conditional / reversal) relationship rather than a persistent tilt.
    """
    lines = [f"\n=== per-period IC -- {title} ===",
             f"{'config':<26}{'periods':>8}{'meanIC':>8}{'stdIC':>8}"
             f"{'t':>7}{'negFrac':>9}{'posFrac':>9}"]
    for c in cards:
        pp = c.ppic
        if pp is None or pp.n_periods == 0:
            lines.append(f"{c.name:<26}{'n/a':>8}")
            continue
        lines.append(
            f"{c.name:<26}{pp.n_periods:>8}{pp.mean_ic:>8.3f}{pp.std_ic:>8.3f}"
            f"{pp.t_stat:>7.2f}{pp.frac_negative:>9.2f}{pp.frac_positive:>9.2f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import sys
    import warnings

    try:  # keep unicode-safe on Windows cp949 consoles
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    warnings.filterwarnings("ignore", message="Mean of empty slice")
    ap = argparse.ArgumentParser(
        description="Bridge-2 interaction/conditional-reversal fusion bake-off (direction target)"
    )
    ap.add_argument("--method", choices=["A", "B", "both"], default="both")
    ap.add_argument("--prices", default="prices_krx250.csv")
    ap.add_argument("--search", default="stockname_daily_krx250.csv")
    ap.add_argument("--revenue", default="dart_krx250.csv")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--horizons", default="20,60",
                    help="comma-separated forward horizons (sessions); step==horizon (non-overlap)")
    ap.add_argument("--embargo-days", type=int, default=None,
                    help="purge gap in calendar days (default ~horizon in trading days)")
    ap.add_argument("--smallcap-quantile", type=float, default=0.33,
                    help="bottom liquidity quantile (median volume) treated as small-cap")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--perm", type=int, default=500, help="permutation iterations")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit-tickers", type=int, default=0, help="0=all (debug subsample)")
    ap.add_argument("--legacy", action="store_true",
                    help="also run the Milestone-1 regression A/B path for reference")
    args = ap.parse_args(argv)

    horizons = [int(h) for h in str(args.horizons).split(",") if h.strip()]
    methods = ["A", "B"] if args.method == "both" else [args.method]

    prices_by_ticker = load_prices_volume_csv(args.prices)
    search_by_ticker = aggregate_search_by_ticker(_load_search(args.search))
    revenue_by_ticker = load_annual_revenue(args.revenue)

    tickers = sorted(set(prices_by_ticker) & set(search_by_ticker))
    if args.limit_tickers:
        tickers = tickers[: args.limit_tickers]

    all_cards: list[ScoreCard] = []
    tagged: list[tuple[str, list[ScoreCard]]] = []  # (title, cards) for rendering
    for horizon in horizons:
        # Non-overlapping forward windows per stock: sample every ``horizon`` sessions.
        signal_step = horizon
        embargo_days = args.embargo_days if args.embargo_days is not None else math.ceil(
            horizon * 7 / 5) + 3
        periods_per_year = 252.0 / signal_step

        panel = build_panel(
            prices_by_ticker=prices_by_ticker,
            search_by_ticker=search_by_ticker,
            revenue_by_ticker=revenue_by_ticker,
            tickers=tickers,
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            horizon=horizon,
            signal_step=signal_step,
        )
        if not panel.rows:
            print(f"[h={horizon}] empty panel — skipped")
            continue
        small_mask, n_small = _smallcap_mask(panel, prices_by_ticker, args.smallcap_quantile)
        universes: list[tuple[str, np.ndarray | None]] = [("all", None), ("small", small_mask)]
        rev_cov = sum(1 for r in panel.rows if math.isfinite(r.revenue_yoy))
        print(
            f"\n[panel h={horizon}] rows={len(panel.rows)} tickers={len(tickers)} "
            f"dates={len(set(r.as_of for r in panel.rows))} "
            f"step={signal_step}(non-overlap) embargo_days={embargo_days} "
            f"smallcap={n_small}tk/{int(small_mask.sum())}rows\n"
            f"  revenue_label_coverage={rev_cov}/{len(panel.rows)} "
            f"({100*rev_cov/max(1,len(panel.rows)):.0f}%) dropped={dict(panel.dropped)}"
        )

        for m in methods:
            cards = run_interaction_meta(
                panel, method=m, n_folds=args.folds, embargo_days=embargo_days,
                seed=args.seed, n_perm=args.perm, periods_per_year=periods_per_year,
                universes=universes,
            )
            for c in cards:
                c.name = f"{m}:{c.name}"  # e.g. A:all:ridge_ix / B:small:gbm_ix
            tagged.append((f"h={horizon} Method {m}", cards))
            all_cards.extend(cards)

        if args.legacy:
            for m in methods:
                lc = run_method(
                    panel, method=m, n_folds=args.folds, embargo_days=embargo_days,
                    seed=args.seed, n_perm=args.perm, periods_per_year=periods_per_year,
                )
                for c in lc:
                    c.name = f"L{m}:{c.name}"
                tagged.append((f"h={horizon} LEGACY-regress {m}", lc))
                all_cards.extend(lc)

    if not all_cards:
        raise SystemExit("no scorable configs — check --prices/--search cover the window")

    _apply_fdr(all_cards, q=0.10)  # BH across EVERY config in the whole bake-off
    for title, cards in tagged:
        print(_render(title, cards))
    for title, cards in tagged:
        print(_render_ppic(title, cards))
    print(
        "\nLegend: IC/rankIC=corr(bullish score, fwd excess ret); hit=direction "
        "accuracy; decSpr=top-bottom decile mean ret; Sharpe=long-short; wfIC="
        "within-firm rankIC; perm_p=within-date permutation; FDR=survives BH q=0.10 "
        "across ALL configs (every horizon×method×universe).\n"
        "Meta configs: ridge_main=linear main-effect (Milestone-1 control); "
        "ridge_ix=linear + explicit mom×att / sgn(mom)×att / mom×liq products; "
        "gbm_main=boosted meta, interactions FORBIDDEN; gbm_ix=boosted meta, "
        "interactions ALLOWED; eqwt=equal-weight base preds (A only)."
    )
    return 0


def _load_search(path: str) -> dict[tuple[str, str], list[tuple[date, float]]]:
    from .period_keyword_dataset import load_keyword_series

    return load_keyword_series(path)


if __name__ == "__main__":
    raise SystemExit(main())
