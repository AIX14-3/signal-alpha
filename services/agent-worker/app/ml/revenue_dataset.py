"""Build a REGRESSION dataset whose target is the NEXT ANNUAL REVENUE YoY, from
name-search — the "search → next-print revenue nowcast" (method A2b).

This is the one directional-ish DataLab relationship that cleared rigorous
validation as a *level nowcaster* (NOT a stock-return alpha):

    검색 abnormal(lag1) → 차기분기/연 매출YoY = 횡단면 robust 양성
    (219종목, permutation + BH-FDR 9/10 생존, IC +0.051 t2.73, q0.002; 단
     횡단면(또래대비)이지 within-firm 아님, 2021~23 era 집중)
    — docs/experiments/2026-06-30-datalab-revenue-retest-universe-expansion.md

The predictor mirrors ``magnitude_dataset`` (the validated attention feature set):
the stock-name search series is forward-filled onto trading days (point-in-time —
only values observed ``<= as_of``) and turned into a trailing rolling z-score
``abn`` over the prior ``WIN`` trading days, plus its momentum, the raw level and
observation recency. The LABEL is the YoY growth of the soonest annual revenue
print that becomes public *after* ``as_of`` (``lag``-th upcoming print), reusing
the PIT filing-date proxy and loader already shipped in ``bakeoff_ab``.

The dataset writes the continuous YoY target into BOTH ``y`` (what the regressor
fits) and ``excess_returns`` (what ``sweep.run_cell`` scores rank-IC against), so
the sweep engine's cross-sectional ``per_period_ic`` measures nowcast skill and
``within_firm_ic`` exposes the static-trait-vs-timing decomposition — no engine
change needed. Same leakage guards as the other builders (features ``<= as_of``,
label strictly after).

Pure Python + numpy; callers pass already-loaded series so the transform is
deterministic and unit-testable without a DB.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date

import numpy as np

from .bakeoff_ab import _filing_date
from .datalab_dataset import Dataset
from .features import build_feature_row, feature_matrix
from .magnitude_dataset import WIN, _ffill_to_trading_days, _rolling_z

# The 2026-06-30 retest winsorized YoY to this band before scoring (heavy right
# tail from tiny prior-year bases). Mirror it so the demo/db paths agree.
_YOY_CLIP = (-0.9, 3.0)


def _revenue_yoy_at_lag(rev: list[tuple[int, float]], as_of: date, lag: int) -> float:
    """YoY growth of the ``lag``-th annual print relative to ``as_of``.

    ``lag>=1`` = the ``lag``-th print whose filing date is strictly AFTER ``as_of``
    (lag1 == the soonest upcoming print, identical to ``bakeoff_ab._next_revenue_yoy``);
    ``lag==0`` = the most recent print already public on/before ``as_of`` (concurrent
    nowcast). ``nan`` when that print or its prior-year base is missing.
    """
    by_year = dict(rev)
    filings = sorted((_filing_date(y), y, a) for y, a in rev)
    if lag >= 1:
        future = [f for f in filings if f[0] > as_of]
        if len(future) < lag:
            return math.nan
        _, year, amount = future[lag - 1]
    else:
        past = [f for f in filings if f[0] <= as_of]
        if not past:
            return math.nan
        _, year, amount = past[-1]
    prev = by_year.get(year - 1)
    if prev and prev > 0 and amount > 0:
        return amount / prev - 1.0
    return math.nan


def build_revenue_dataset(
    *,
    search_by_ticker: dict[str, list[tuple[date, float]]],
    revenue_by_ticker: dict[str, list[tuple[int, float]]],
    trading_days_by_ticker: dict[str, list[date]],
    signal_dates_by_ticker: dict[str, list[date]],
    lag: int = 1,
    mom_lag: int = 5,
    win: int = WIN,
) -> Dataset:
    """Assemble per-(ticker, signal-date) search features + a next-print revenue-YoY y.

    A sample is kept only when the search ``abn`` is defined at ``as_of`` (enough
    history) AND the ``lag``-th upcoming revenue print (and its prior-year base)
    exists. Every drop is counted in ``Dataset.dropped`` so a small surviving
    sample can't pass silently. The YoY target is winsorized to ``_YOY_CLIP``.
    """
    ticker_code = {t: i for i, t in enumerate(sorted(signal_dates_by_ticker))}
    feat_rows: list[dict[str, float]] = []
    y: list[float] = []
    dates: list[int] = []
    stock_ids: list[int] = []
    dropped: Counter = Counter()

    for ticker, signal_dates in signal_dates_by_ticker.items():
        td = trading_days_by_ticker.get(ticker)
        if not td:
            dropped["no_calendar"] += len(signal_dates)
            continue
        rev = revenue_by_ticker.get(ticker, [])
        td_index = {d: i for i, d in enumerate(td)}
        level_by_day = _ffill_to_trading_days(td, search_by_ticker.get(ticker, []))
        abn = _rolling_z(td, level_by_day, win=win)
        for as_of in signal_dates:
            if as_of not in abn:
                dropped["no_abn"] += 1
                continue
            yoy = _revenue_yoy_at_lag(rev, as_of, lag)
            if not math.isfinite(yoy):
                dropped["no_revenue_label"] += 1
                continue
            yoy = float(min(max(yoy, _YOY_CLIP[0]), _YOY_CLIP[1]))
            i = td_index[as_of]
            lag_day = td[i - mom_lag] if i - mom_lag >= 0 else None
            abn_mom = (
                abn[as_of] - abn[lag_day]
                if (lag_day is not None and lag_day in abn)
                else 0.0
            )
            level, observed_date = level_by_day[as_of]
            mapping = {
                "abn": abn[as_of],
                "abn_mom": abn_mom,
                "search_level": level,
                "obs_age": float((as_of - observed_date).days),
            }
            feat_rows.append(build_feature_row("search", mapping))
            y.append(yoy)
            dates.append(as_of.toordinal())
            stock_ids.append(ticker_code[ticker])

    matrix, names = feature_matrix(feat_rows)
    target = np.array(y, dtype=float)
    return Dataset(
        X=np.array(matrix, dtype=float).reshape(len(feat_rows), len(names)),
        y=target,
        # The sweep scores IC of predicted vs realized revenue-YoY, so the
        # "returns" array IS the continuous revenue target (mirrors magnitude).
        excess_returns=target.copy(),
        dates=np.array(dates, dtype=int),
        stock_ids=np.array(stock_ids, dtype=int),
        feature_names=names,
        dropped=dropped,
    )


def load_revenue_dataset(
    *,
    keyword_csv: str,
    prices_csv: str,
    revenue_csv: str,
    start: date,
    end: date,
    tickers: list[str] | None = None,
    lag: int = 1,
    signal_step: int = 5,
    mom_lag: int = 5,
    win: int = WIN,
) -> Dataset:
    """Load name-search + annual-revenue CSVs and build the revenue-nowcast Dataset.

    Mirrors ``magnitude_dataset.load_magnitude_dataset`` but the target is next-print
    revenue-YoY (``bakeoff_ab.load_annual_revenue`` for the label). The trading-day
    calendar (for the search rolling-z) comes from the prices CSV. ``tickers=None``
    uses every ticker present in the search AND price files. No DB.
    """
    from datetime import date as _date  # noqa: F401 - keep date import explicit

    from .bakeoff_ab import load_annual_revenue
    from .datalab_dataset import weekly_signal_dates
    from .magnitude_dataset import aggregate_search_by_ticker
    from .period_keyword_dataset import load_keyword_series
    from .prices_csv import load_prices_csv

    search_by_ticker = aggregate_search_by_ticker(load_keyword_series(keyword_csv))
    prices_by_ticker = load_prices_csv(prices_csv)
    revenue_by_ticker = load_annual_revenue(revenue_csv)
    if not tickers:
        tickers = sorted(set(search_by_ticker) & set(prices_by_ticker))

    trading_days_by_ticker: dict[str, list[date]] = {}
    signal_dates_by_ticker: dict[str, list[date]] = {}
    for ticker in tickers:
        prices = prices_by_ticker.get(ticker)
        if prices is None:
            continue
        trading_days_by_ticker[ticker] = prices.dates
        in_window = [d for d in prices.dates if start <= d <= end]
        signal_dates_by_ticker[ticker] = weekly_signal_dates(in_window, step=signal_step)

    return build_revenue_dataset(
        search_by_ticker=search_by_ticker,
        revenue_by_ticker=revenue_by_ticker,
        trading_days_by_ticker=trading_days_by_ticker,
        signal_dates_by_ticker=signal_dates_by_ticker,
        lag=lag,
        mom_lag=mom_lag,
        win=win,
    )
