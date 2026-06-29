"""Build a real (X, y) dataset for the bake-off from HIRING postings + prices.

Mirrors ``datalab_dataset`` but for hiring. Hiring is *event-like and strongly
seasonal* (Korean 정기공채: spring/fall peaks), so raw posting counts encode the
calendar, not company-specific demand. This module therefore engineers a small,
de-seasonalized, point-in-time feature set instead of feeding raw counts:

  hiring__deseason_momentum  recent-half vs prior-half de-seasonalized posting flow
  hiring__yoy_change         this window's flow vs the same window one year earlier
  hiring__days_since_latest  recency of the latest posting known at ``as_of``

Leakage guards (non-negotiable, same as DataLab):
1. Features at ``as_of`` use only postings with ``observed_date <= as_of`` (the
   posting's KST publish date is the knowledge time).
2. The label uses only prices STRICTLY AFTER ``as_of``.

The seasonal index is an empirical monthly factor estimated from the supplied
postings (pooled across stocks for stability given the tiny per-stock sample). It
normalizes a *feature* (calendar structure), not the label, so its in-sample
nature does not leak the return signal.

Pure Python + numpy: callers pass already-fetched rows (see ``hiring_db``), so the
transformation is deterministic and unit-testable without a DB.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

# Reuse the DataLab harness primitives verbatim (no parallel re-implementation).
from .datalab_dataset import Dataset, PriceSeries, _as_date, weekly_signal_dates
from .features import feature_matrix
from .labels import make_label

_YEAR_DAYS = 365


def seasonal_index(observed_dates: list[date]) -> dict[int, float]:
    """Empirical month-of-year factor: ``month_share / uniform_share`` (mean ≈ 1).

    A factor > 1 means that calendar month is busier than average (e.g. 공채철),
    so de-seasonalizing divides posting weight by it. Months with no data or a
    non-positive factor fall back to 1.0 (no-op) so they never blow up a ratio.
    """
    counts = Counter(d.month for d in observed_dates)
    total = sum(counts.values())
    if total == 0:
        return {m: 1.0 for m in range(1, 13)}
    uniform = total / 12.0
    factors: dict[int, float] = {}
    for m in range(1, 13):
        f = counts.get(m, 0) / uniform if uniform > 0 else 1.0
        factors[m] = f if f > 0 else 1.0
    return factors


def _factor(factors: dict[int, float], month: int) -> float:
    f = factors.get(month, 1.0)
    return f if f > 0 else 1.0


def _wsum(dates: list[date], factors: dict[int, float]) -> float:
    """De-seasonalized posting weight: sum of 1/seasonal_factor over postings."""
    return sum(1.0 / _factor(factors, d.month) for d in dates)


def hiring_features(
    dates_sorted: list[date],
    *,
    as_of: date,
    lookback_days: int,
    factors: dict[int, float],
) -> tuple[dict[str, float], int]:
    """Point-in-time hiring features at ``as_of`` and the in-window posting count.

    ``dates_sorted`` is ALL of one stock's posting dates ascending; we slice
    point-in-time windows here (yoy needs dates older than the lookback window,
    so the full list is passed rather than a pre-sliced window).
    """
    lo = as_of - timedelta(days=lookback_days)
    window = [d for d in dates_sorted if lo < d <= as_of]
    n = len(window)

    # recent vs prior half (de-seasonalized) -> momentum
    mid = lo + (as_of - lo) / 2
    recent = [d for d in window if d > mid]
    prior = [d for d in window if d <= mid]
    rw, pw = _wsum(recent, factors), _wsum(prior, factors)
    momentum = (rw - pw) / pw if pw > 0 else float("nan")

    # this window vs same window one year earlier -> year-over-year change
    y_lo = as_of - timedelta(days=_YEAR_DAYS + lookback_days)
    y_hi = as_of - timedelta(days=_YEAR_DAYS)
    prev_window = [d for d in dates_sorted if y_lo < d <= y_hi]
    cur_w, prev_w = _wsum(window, factors), _wsum(prev_window, factors)
    yoy = (cur_w - prev_w) / prev_w if prev_w > 0 else float("nan")

    # recency of the latest posting known at as_of
    known = [d for d in dates_sorted if d <= as_of]
    days_since = float((as_of - max(known)).days) if known else float("nan")

    features = {
        "hiring__deseason_momentum": momentum,
        "hiring__yoy_change": yoy,
        "hiring__days_since_latest": days_since,
    }
    return features, n


def build_dataset(
    *,
    hiring_rows_by_stock: dict[int, list[dict]],
    prices_by_stock: dict[int, PriceSeries],
    signal_dates_by_stock: dict[int, list[date]],
    benchmark: PriceSeries | None = None,
    lookback_days: int = 90,
    horizon_sessions: int = 20,
    neutral_band_pct: float = 0.3,
    min_observations: int = 2,
    seasonal: dict[int, float] | None = None,
) -> Dataset:
    """Assemble per-(stock, signal-date) hiring feature rows + forward-return labels.

    A sample is kept only when the lookback window has >= ``min_observations``
    postings AND a valid forward return AND a non-neutral direction; every drop is
    counted in ``Dataset.dropped`` so a tiny surviving sample can't pass silently.
    The seasonal index is estimated once from ALL supplied postings (pooled).
    """
    # Parse + sort each stock's posting dates once; pool all for the seasonal index.
    dates_by_stock: dict[int, list[date]] = {}
    pooled: list[date] = []
    for stock_id, rows in hiring_rows_by_stock.items():
        ds = sorted(d for r in rows if (d := _as_date(r.get("observed_date"))) is not None)
        dates_by_stock[stock_id] = ds
        pooled.extend(ds)
    factors = seasonal if seasonal is not None else seasonal_index(pooled)

    feat_rows: list[dict[str, float]] = []
    y: list[int] = []
    excess: list[float] = []
    dates: list[int] = []
    stock_ids: list[int] = []
    dropped: Counter = Counter()

    for stock_id, signal_dates in signal_dates_by_stock.items():
        dates_sorted = dates_by_stock.get(stock_id, [])
        prices = prices_by_stock.get(stock_id)
        if prices is None:
            dropped["no_price_series"] += len(signal_dates)
            continue
        for as_of in signal_dates:
            features, n = hiring_features(
                dates_sorted, as_of=as_of, lookback_days=lookback_days, factors=factors
            )
            if n < min_observations:
                dropped["too_few_observations"] += 1
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
                bench_ret = 0.0  # benchmark gap -> fall back to raw return
            label = make_label(
                stock_return_pct=stock_ret,
                benchmark_return_pct=bench_ret,
                neutral_band_pct=neutral_band_pct,
            )
            if label.y_direction is None:
                dropped["neutral_band"] += 1
                continue
            feat_rows.append(features)
            y.append(label.y_direction)
            excess.append(label.excess_return_pct)
            dates.append(as_of.toordinal())
            stock_ids.append(stock_id)

    matrix, names = feature_matrix(feat_rows)
    return Dataset(
        X=np.array(matrix, dtype=float).reshape(len(feat_rows), len(names)),
        y=np.array(y, dtype=int),
        excess_returns=np.array(excess, dtype=float),
        dates=np.array(dates, dtype=int),
        stock_ids=np.array(stock_ids, dtype=int),
        feature_names=names,
        dropped=dropped,
    )


__all__ = [
    "build_dataset",
    "hiring_features",
    "seasonal_index",
    "weekly_signal_dates",
]
