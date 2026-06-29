"""Build a real (X, y) dataset for the bake-off from PATENT + price rows.

Mirrors ``datalab_dataset`` but for patents, reusing the SHIPPING feature logic
(``analyzers/patent/indicators.compute_indicators``) and the harness labels.

LOOK-AHEAD GUARD (critical, differs from production analyzer): patents are SECRET
for ~18 months after filing, so the only honest "knowable at as_of" timestamp is
the PUBLICATION date, not the application/filing date. The DB adapter
(``patent_db``) therefore stamps each shaped row's ``application_date`` field with
its *publication* date, so both the lookback window and ``compute_indicators``'
recent/prior momentum run on the publication timeline. (Production's PatentAnalyzer
windows by filing date — fine for a live score, but a forward-return backtest must
not use filings the market could not yet see.)

Pure Python + numpy: callers pass already-fetched rows, keeping this deterministic
and unit-testable without a DB.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta

import numpy as np
from scipy.stats import rankdata

from app.analyzers.patent.indicators import compute_indicators

from .datalab_dataset import Dataset, PriceSeries, _as_date
from .features import build_feature_row, feature_matrix
from .labels import make_label

# Absolute-scale, size-confounded count columns. Left un-normalized, a model can
# read these as a proxy for company size (Samsung's ``total`` dwarfs a small-cap's
# by ~1000x) and "learn" stock identity instead of any patent signal. Cross-
# sectional normalization (rank/z within each date) removes that level so only the
# *relative* standing across stocks survives — the only thing a rankIC should see.
_COUNT_FEATURES = frozenset(
    {
        "patent__total",
        "patent__recent_count",
        "patent__prior_count",
        "patent__distinct_tech_categories",
        "patent__days_since_latest",
        "patent__new_category_count",
        "patent__llm_enriched_count",
    }
)


def _cross_sectional_normalize(
    X: np.ndarray, dates: np.ndarray, names: list[str], method: str
) -> np.ndarray:
    """Within each date, rank/z-score the size-confounded count columns across stocks.

    ``method``: ``"none"`` (passthrough), ``"rank"`` (percentile in [0,1], average
    ties), or ``"zscore"``. Ratio/score columns are left untouched. NaNs are
    preserved and excluded from the per-date statistic (ranked among non-NaN only),
    so a stock missing a feature on a date stays missing rather than getting a
    spurious mid-rank.
    """
    if method == "none" or X.size == 0:
        return X
    cols = [i for i, n in enumerate(names) if n in _COUNT_FEATURES]
    if not cols:
        return X
    out = X.copy()
    for d in np.unique(dates):
        idx = np.where(dates == d)[0]
        if len(idx) < 2:
            continue
        for c in cols:
            vals = out[idx, c]
            mask = ~np.isnan(vals)
            if mask.sum() < 2:
                continue
            v = vals[mask]
            if method == "rank":
                vals[mask] = (rankdata(v) - 1.0) / (mask.sum() - 1)
            elif method == "zscore":
                sd = v.std()
                vals[mask] = (v - v.mean()) / sd if sd > 0 else 0.0
            out[idx, c] = vals
    return out


def _window_rows(rows: list[dict], *, as_of: date, lookback_days: int) -> list[dict]:
    """Patent rows PUBLICLY KNOWN at ``as_of``: publication in (as_of - lookback, as_of].

    Uses ``publication_date`` (when the filing became public) — the leakage guard
    for a forward-return backtest. Rows without a parseable publication date are
    dropped (they have no honest knowable timestamp).
    """
    lo = as_of - timedelta(days=lookback_days)
    out = []
    for row in rows:
        pub = _as_date(row.get("publication_date"))
        if pub is not None and lo < pub <= as_of:
            out.append(row)
    return out


def build_dataset(
    *,
    patent_rows_by_stock: dict[int, list[dict]],
    prices_by_stock: dict[int, PriceSeries],
    signal_dates_by_stock: dict[int, list[date]],
    benchmark: PriceSeries | None = None,
    lookback_days: int = 60,
    horizon_sessions: int = 5,
    neutral_band_pct: float = 0.3,
    min_observations: int = 1,
    xs_normalize: str = "none",
    exclude_features: frozenset[str] = frozenset(),
) -> Dataset:
    """Assemble per-(stock, signal-date) patent feature rows and forward-return labels.

    Mirrors ``datalab_dataset.build_dataset`` but windows by publication date,
    calls the patent indicators, and defaults ``min_observations=1`` (patent
    filings are far sparser than DataLab daily observations). Every drop is counted
    in ``Dataset.dropped`` so a thin surviving sample can't pass silently.

    ``xs_normalize`` (``none``/``rank``/``zscore``) controls cross-sectional
    neutralization of the size-confounded count columns (see
    ``_cross_sectional_normalize``). ``exclude_features`` drops feature columns by
    their prefixed name (e.g. ``patent__mean_significance``) — used to run a clean
    count-only test when LLM-enriched columns are mostly empty.
    """
    feat_rows: list[dict[str, float]] = []
    y: list[int] = []
    excess: list[float] = []
    dates: list[int] = []
    stock_ids: list[int] = []
    dropped: Counter = Counter()

    for stock_id, signal_dates in signal_dates_by_stock.items():
        rows = patent_rows_by_stock.get(stock_id, [])
        prices = prices_by_stock.get(stock_id)
        if prices is None:
            dropped["no_price_series"] += len(signal_dates)
            continue
        for as_of in signal_dates:
            window = _window_rows(rows, as_of=as_of, lookback_days=lookback_days)
            indicators = compute_indicators(
                window, as_of=as_of, lookback_days=lookback_days
            )
            if indicators.total < min_observations:
                dropped["too_few_filings"] += 1
                continue
            stock_ret = prices.forward_return_pct(as_of, horizon_sessions)
            if stock_ret is None:
                dropped["no_forward_price"] += 1
                continue
            bench_ret = (
                benchmark.forward_return_pct(as_of, horizon_sessions)
                if benchmark is not None
                else 0.0
            )
            if bench_ret is None:
                bench_ret = 0.0
            label = make_label(
                stock_return_pct=stock_ret,
                benchmark_return_pct=bench_ret,
                neutral_band_pct=neutral_band_pct,
            )
            if label.y_direction is None:
                dropped["neutral_band"] += 1
                continue
            # Drop the non-numeric date string (would become an all-NaN feature column).
            ind = asdict(indicators)
            ind.pop("latest_application_date", None)
            feat_rows.append(build_feature_row("patent", ind))
            y.append(label.y_direction)
            excess.append(label.excess_return_pct)
            dates.append(as_of.toordinal())
            stock_ids.append(stock_id)

    matrix, names = feature_matrix(feat_rows)
    X = (
        np.array(matrix, dtype=float).reshape(len(feat_rows), len(names))
        if feat_rows
        else np.empty((0, 0))
    )
    dates_arr = np.array(dates, dtype=int)
    if exclude_features and names:
        keep = [i for i, n in enumerate(names) if n not in exclude_features]
        names = [names[i] for i in keep]
        X = X[:, keep] if X.size else X
    X = _cross_sectional_normalize(X, dates_arr, names, xs_normalize)
    return Dataset(
        X=X,
        y=np.array(y, dtype=int),
        excess_returns=np.array(excess, dtype=float),
        dates=dates_arr,
        stock_ids=np.array(stock_ids, dtype=int),
        feature_names=names,
        dropped=dropped,
    )


__all__ = ["build_dataset", "_window_rows"]
