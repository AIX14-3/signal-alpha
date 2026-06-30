"""Build a REGRESSION dataset whose target is future MAGNITUDE, from name-search.

The directional builders (``datalab_dataset`` / ``period_keyword_dataset``) predict
the SIGN of the forward excess return. This module predicts a non-directional
magnitude instead — the one DataLab→price relationship that survived rigorous
validation (non-overlapping cross-sectional IC +0.15~0.30, permutation + BH-FDR
q≈0; see ``docs/attention-spike-flag-design.md``):

- ``volatility`` target = forward realized volatility (``labels.forward_realized_volatility``)
- ``volume``     target = forward abnormal volume    (``labels.forward_abnormal_volume``)

The predictor mirrors ``scripts/search_to_magnitude``: the stock-name DataLab
search series is forward-filled onto trading days (point-in-time — only values
observed ``<= as_of``) and turned into a trailing rolling z-score ``abn`` over the
prior ``WIN`` trading days. That ``abn`` is the single feature that cleared the
bar; here we hand the bake-off a small PIT feature vector around it (``abn``,
its momentum, the raw search level, and observation recency) so the model
selection asks the honest question: *can any ML model, on these search-only
features, predict forward magnitude better than a mean baseline — and beat the
single-``abn`` IC?*

Two leakage guards, identical in spirit to the directional builders:
1. Features at ``as_of`` use only search values observed ``<= as_of`` and a
   strictly-past rolling window.
2. The target uses only prices STRICTLY AFTER ``as_of`` (forward closes/volumes),
   with the volume baseline drawn strictly BEFORE it.

Pure Python + numpy; callers pass already-loaded series, so the transform is
deterministic and unit-testable without a DB.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import date

import numpy as np

from .datalab_dataset import Dataset, PriceSeries, weekly_signal_dates
from .features import build_feature_row, feature_matrix
from .labels import forward_abnormal_volume, forward_realized_volatility
from .period_keyword_dataset import load_keyword_series

# Trailing trading-day window for the rolling z (mirrors search_to_magnitude.WIN).
WIN = 60
TARGETS = ("volatility", "volume")


def aggregate_search_by_ticker(
    series_by_kw: dict[tuple[str, str], list[tuple[date, float]]],
) -> dict[str, list[tuple[date, float]]]:
    """Collapse ``{(ticker, keyword): series}`` into one search series per ticker.

    The name-search CSV has a single keyword (the stock name) per ticker, but if
    several keywords exist we average their ratios per date so the result is one
    ascending ``[(date, level)]`` per ticker.
    """
    by_ticker: dict[str, dict[date, list[float]]] = {}
    for (ticker, _keyword), points in series_by_kw.items():
        per_date = by_ticker.setdefault(ticker, {})
        for d, ratio in points:
            per_date.setdefault(d, []).append(ratio)
    out: dict[str, list[tuple[date, float]]] = {}
    for ticker, per_date in by_ticker.items():
        out[ticker] = sorted((d, sum(v) / len(v)) for d, v in per_date.items())
    return out


def _ffill_to_trading_days(
    trading_days: list[date], points: list[tuple[date, float]]
) -> dict[date, tuple[float, date]]:
    """Carry the last search value observed ``<= d`` onto every trading day ``d``.

    Returns ``{trading_day: (level, observed_date)}`` — point-in-time, so a value
    is only ever carried FORWARD from a date on/before the trading day. Trading
    days before the first observation are absent (no value is knowable yet).
    """
    pts = sorted(points)
    out: dict[date, tuple[float, date]] = {}
    j = 0
    last: float | None = None
    last_date: date | None = None
    for d in trading_days:
        while j < len(pts) and pts[j][0] <= d:
            last, last_date = pts[j][1], pts[j][0]
            j += 1
        if last is not None and last_date is not None:
            out[d] = (last, last_date)
    return out


def _rolling_z(
    trading_days: list[date],
    level_by_day: dict[date, tuple[float, date]],
    win: int = WIN,
) -> dict[date, float]:
    """Trailing rolling z-score of the search level (mirrors search_to_magnitude).

    The window is the STRICTLY-PRIOR ``win`` trading days (``range(i-win, i)``), so
    there is no look-ahead. Needs ``>= win//2`` historical points and a non-zero
    stdev, else the day is omitted.
    """
    out: dict[date, float] = {}
    for i, d in enumerate(trading_days):
        if i < win // 2 or d not in level_by_day:
            continue
        hist = [
            level_by_day[trading_days[j]][0]
            for j in range(max(0, i - win), i)
            if trading_days[j] in level_by_day
        ]
        if len(hist) < win // 2:
            continue
        mu = statistics.mean(hist)
        sd = statistics.pstdev(hist)
        if sd > 0:
            out[d] = (level_by_day[d][0] - mu) / sd
    return out


def _magnitude_target(
    prices: PriceSeries, as_of: date, *, target: str, horizon: int, baseline_back: int
) -> float | None:
    """Forward realized volatility (``volatility``) or abnormal volume (``volume``)."""
    if target == "volatility":
        fwd = prices.forward_closes(as_of, horizon)
        return forward_realized_volatility(fwd) if fwd else None
    fwd_vol = prices.forward_volumes(as_of, horizon)
    base_vol = prices.baseline_volumes(as_of, baseline_back)
    if not fwd_vol or not base_vol:
        return None
    return forward_abnormal_volume(fwd_vol, base_vol)


def build_magnitude_dataset(
    *,
    search_by_ticker: dict[str, list[tuple[date, float]]],
    prices_by_ticker: dict[str, PriceSeries],
    signal_dates_by_ticker: dict[str, list[date]],
    target: str = "volatility",
    horizon_sessions: int = 5,
    baseline_back: int = WIN,
    mom_lag: int = 5,
    win: int = WIN,
) -> Dataset:
    """Assemble per-(ticker, signal-date) search features + a continuous magnitude y.

    ``y`` is the realized forward magnitude (unsigned, continuous) — there is no
    neutral band (meaningless for a magnitude); a sample is dropped only when the
    ``abn`` feature is undefined (too little history) or the forward window is too
    short. Every drop is counted in ``Dataset.dropped``.
    """
    if target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS}, got {target!r}")

    ticker_code = {t: i for i, t in enumerate(sorted(signal_dates_by_ticker))}
    feat_rows: list[dict[str, float]] = []
    y: list[float] = []
    dates: list[int] = []
    stock_ids: list[int] = []
    dropped: Counter = Counter()

    for ticker, signal_dates in signal_dates_by_ticker.items():
        prices = prices_by_ticker.get(ticker)
        if prices is None:
            dropped["no_price_series"] += len(signal_dates)
            continue
        td = prices.dates
        td_index = {d: i for i, d in enumerate(td)}
        level_by_day = _ffill_to_trading_days(td, search_by_ticker.get(ticker, []))
        abn = _rolling_z(td, level_by_day, win=win)
        for as_of in signal_dates:
            if as_of not in abn:
                dropped["no_abn"] += 1
                continue
            tval = _magnitude_target(
                prices,
                as_of,
                target=target,
                horizon=horizon_sessions,
                baseline_back=baseline_back,
            )
            if tval is None:
                dropped["no_forward_target"] += 1
                continue
            i = td_index[as_of]
            abn_now = abn[as_of]
            lag_day = td[i - mom_lag] if i - mom_lag >= 0 else None
            abn_mom = abn_now - abn[lag_day] if (lag_day is not None and lag_day in abn) else 0.0
            level, observed_date = level_by_day[as_of]
            mapping = {
                "abn": abn_now,
                "abn_mom": abn_mom,
                "search_level": level,
                "obs_age": float((as_of - observed_date).days),
            }
            feat_rows.append(build_feature_row("magnitude", mapping))
            y.append(float(tval))
            dates.append(as_of.toordinal())
            stock_ids.append(ticker_code[ticker])

    matrix, names = feature_matrix(feat_rows)
    target_arr = np.array(y, dtype=float)
    return Dataset(
        X=np.array(matrix, dtype=float).reshape(len(feat_rows), len(names)),
        y=target_arr,
        # The bake-off computes IC of predicted vs realized magnitude, so the
        # "returns" array IS the magnitude target here (not a signed return).
        excess_returns=target_arr.copy(),
        dates=np.array(dates, dtype=int),
        stock_ids=np.array(stock_ids, dtype=int),
        feature_names=names,
        dropped=dropped,
    )


def load_magnitude_dataset(
    *,
    keyword_csv: str,
    prices_csv: str,
    start: date,
    end: date,
    tickers: list[str] | None = None,
    target: str = "volatility",
    horizon_sessions: int = 5,
    signal_step: int = 5,
    baseline_back: int = WIN,
    mom_lag: int = 5,
    win: int = WIN,
) -> Dataset:
    """Load name-search ratios + price/volume CSV and build the magnitude Dataset.

    ``tickers=None`` uses every ticker present in BOTH the search and price files
    (the validated KOSDAQ universe). Signal dates are the trading days in
    ``[start, end]`` sampled every ``signal_step`` sessions (weekly by default).
    """
    from .prices_csv import load_prices_volume_csv

    search_by_ticker = aggregate_search_by_ticker(load_keyword_series(keyword_csv))
    prices_by_ticker = load_prices_volume_csv(prices_csv)
    if not tickers:
        tickers = sorted(set(search_by_ticker) & set(prices_by_ticker))

    signal_dates_by_ticker: dict[str, list[date]] = {}
    prices_subset: dict[str, PriceSeries] = {}
    for ticker in tickers:
        prices = prices_by_ticker.get(ticker)
        if prices is None:
            signal_dates_by_ticker[ticker] = []
            continue
        prices_subset[ticker] = prices
        in_window = [d for d in prices.dates if start <= d <= end]
        signal_dates_by_ticker[ticker] = weekly_signal_dates(in_window, step=signal_step)

    return build_magnitude_dataset(
        search_by_ticker=search_by_ticker,
        prices_by_ticker=prices_subset,
        signal_dates_by_ticker=signal_dates_by_ticker,
        target=target,
        horizon_sessions=horizon_sessions,
        baseline_back=baseline_back,
        mom_lag=mom_lag,
        win=win,
    )


__all__ = [
    "WIN",
    "TARGETS",
    "aggregate_search_by_ticker",
    "build_magnitude_dataset",
    "load_magnitude_dataset",
]
