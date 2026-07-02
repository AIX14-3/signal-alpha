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

Run:
  python -m app.ml.bakeoff_ab --method both \
      --prices prices_krx250.csv --search stockname_daily_krx250.csv \
      --revenue dart_krx250.csv --horizon 20 --signal-step 10 --perm 500
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
    _decile_spread,
    _safe_corr,
    benjamini_hochberg,
    oos_predictions,
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
    return ScoreCard(name, len(p), ic, ric, hit, ds, sharpe, wfic, perm_p)


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


def _apply_fdr(cards: list[ScoreCard], q: float = 0.10) -> None:
    survive = benjamini_hochberg([c.perm_p for c in cards], q=q)
    for c, s in zip(cards, survive):
        c.fdr_survive = s


def _render(title: str, cards: list[ScoreCard]) -> str:
    lines = [f"\n=== {title} ===",
             f"{'config':<18}{'n':>7}{'IC':>8}{'rankIC':>8}{'hit':>7}"
             f"{'decSpr':>9}{'Sharpe':>8}{'wfIC':>8}{'perm_p':>8}{'FDR':>5}"]
    for c in cards:
        lines.append(
            f"{c.name:<18}{c.n:>7}{c.ic:>8.3f}{c.rank_ic:>8.3f}{c.hit_rate:>7.3f}"
            f"{c.decile_spread:>9.3f}{c.sharpe:>8.2f}{c.within_firm_ic:>8.3f}"
            f"{c.perm_p:>8.3f}{'Y' if c.fdr_survive else '-':>5}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import warnings

    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    warnings.filterwarnings("ignore", message="Mean of empty slice")
    ap = argparse.ArgumentParser(description="Multi-source fusion bake-off (A vs B)")
    ap.add_argument("--method", choices=["A", "B", "both"], default="both")
    ap.add_argument("--prices", default="prices_krx250.csv")
    ap.add_argument("--search", default="stockname_daily_krx250.csv")
    ap.add_argument("--revenue", default="dart_krx250.csv")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--horizon", type=int, default=20, help="forward label horizon (sessions)")
    ap.add_argument("--signal-step", type=int, default=10, help="trading-day gap between signals")
    ap.add_argument("--embargo-days", type=int, default=None,
                    help="purge gap in calendar days (default ~horizon in trading days)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--perm", type=int, default=500, help="permutation iterations")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit-tickers", type=int, default=0, help="0=all (debug subsample)")
    args = ap.parse_args(argv)

    embargo_days = args.embargo_days if args.embargo_days is not None else math.ceil(
        args.horizon * 7 / 5) + 3
    periods_per_year = 252.0 / args.signal_step

    prices_by_ticker = load_prices_volume_csv(args.prices)
    search_by_ticker = aggregate_search_by_ticker(_load_search(args.search))
    revenue_by_ticker = load_annual_revenue(args.revenue)

    tickers = sorted(set(prices_by_ticker) & set(search_by_ticker))
    if args.limit_tickers:
        tickers = tickers[: args.limit_tickers]

    panel = build_panel(
        prices_by_ticker=prices_by_ticker,
        search_by_ticker=search_by_ticker,
        revenue_by_ticker=revenue_by_ticker,
        tickers=tickers,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        horizon=args.horizon,
        signal_step=args.signal_step,
    )
    rev_cov = sum(1 for r in panel.rows if math.isfinite(r.revenue_yoy))
    print(
        f"[panel] rows={len(panel.rows)} tickers={len(tickers)} "
        f"dates={len(set(r.as_of for r in panel.rows))} "
        f"horizon={args.horizon} step={args.signal_step} embargo_days={embargo_days}\n"
        f"  revenue_label_coverage={rev_cov}/{len(panel.rows)} "
        f"({100*rev_cov/max(1,len(panel.rows)):.0f}%)\n"
        f"  dropped={dict(panel.dropped)}"
    )
    if not panel.rows:
        raise SystemExit("empty panel — check --prices/--search cover the window")

    methods = ["A", "B"] if args.method == "both" else [args.method]
    all_cards: list[ScoreCard] = []
    tagged: list[tuple[str, list[ScoreCard]]] = []
    for m in methods:
        cards = run_method(
            panel, method=m, n_folds=args.folds, embargo_days=embargo_days,
            seed=args.seed, n_perm=args.perm, periods_per_year=periods_per_year,
        )
        for c in cards:
            c.name = f"{m}:{c.name}"
        tagged.append((m, cards))
        all_cards.extend(cards)

    _apply_fdr(all_cards, q=0.10)  # BH across every config in the whole bake-off
    for m, cards in tagged:
        print(_render(f"Method {m}", cards))
    print(
        "\nLegend: IC/rankIC=corr(pred,fwd excess ret); hit=direction accuracy; "
        "decSpr=top-bottom decile mean ret; Sharpe=long-short (overlap caveat if "
        "step<horizon); wfIC=within-firm rankIC; perm_p=within-date permutation; "
        "FDR=survives BH q=0.10 across all configs."
    )
    return 0


def _load_search(path: str) -> dict[tuple[str, str], list[tuple[date, float]]]:
    from .period_keyword_dataset import load_keyword_series

    return load_keyword_series(path)


if __name__ == "__main__":
    raise SystemExit(main())
