"""Realistic DataLab+price demo inputs to exercise the real DataLab pipeline.

Unlike ``synthetic`` (which hands the models a ready feature matrix), this builds
actual DataLab *row dicts* and *price series* so the data flows through the SAME
code a DB run would: ``compute_indicators`` -> ``build_feature_row`` -> labels.
That makes it a true plumbing test for ``datalab_dataset.build_dataset``.

A learnable relationship is planted: future return depends on recent search
momentum (plus a market factor and noise), so a working pipeline should let real
models beat the baseline. It says nothing about real markets.

Deterministic given ``seed``; the trading calendar starts at the caller-supplied
``start`` date (no global clock).
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np

from .datalab_dataset import PriceSeries

_DEMAND_KEYWORDS = ["HBM", "D램", "AI 반도체"]
_RISK_KEYWORDS = ["리콜", "불매"]


def _business_days(start: date, n: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:  # Mon-Fri
            days.append(d)
        d += timedelta(days=1)
    return days


def generate_demo(
    *,
    n_stocks: int = 3,
    weeks: int = 104,
    start: date = date(2021, 1, 4),
    seed: int = 11,
    signal_step: int = 5,
):
    """Build (datalab_rows, prices, signal_dates, benchmark) for ``n_stocks``.

    ``weeks`` of business days (~2 years at 104). Returns the exact shapes
    ``datalab_dataset.build_dataset`` expects.
    """
    rng = np.random.default_rng(seed)
    calendar = _business_days(start, weeks * 5)
    n_days = len(calendar)

    # Shared market factor -> benchmark series (so excess return is meaningful).
    market_ret = rng.normal(0.0, 0.8, size=n_days)
    benchmark = PriceSeries.from_pairs(
        list(zip(calendar, 100.0 * np.cumprod(1.0 + market_ret / 100.0)))
    )

    datalab_rows_by_stock: dict[int, list[dict]] = {}
    prices_by_stock: dict[int, PriceSeries] = {}
    signal_dates_by_stock: dict[int, list[date]] = {}

    for stock_id in range(1, n_stocks + 1):
        # Daily search level: mean-reverting random walk with occasional spikes.
        level = np.empty(n_days)
        level[0] = 50.0
        for t in range(1, n_days):
            shock = rng.normal(0, 4) + (rng.random() < 0.04) * rng.normal(25, 8)
            level[t] = max(1.0, 0.92 * level[t - 1] + 0.08 * 50.0 + shock)

        # Search momentum (5d vs prior 5d) -> drives FUTURE return + noise + market.
        momentum = np.zeros(n_days)
        for t in range(10, n_days):
            recent = level[t - 5 : t].mean()
            prior = level[t - 10 : t - 5].mean()
            momentum[t] = (recent - prior) / prior if prior else 0.0
        # Modest coefficient keeps daily returns in a realistic ~+/-1% range
        # (the signal just has to be learnable, not large).
        stock_ret = 0.2 * (momentum * 100.0) + market_ret + rng.normal(0, 1.0, size=n_days)
        prices = PriceSeries.from_pairs(
            list(zip(calendar, 100.0 * np.cumprod(1.0 + stock_ret / 100.0)))
        )
        prices_by_stock[stock_id] = prices

        rows: list[dict] = []
        prev = {kw: 50.0 for kw in _DEMAND_KEYWORDS}
        for t, day in enumerate(calendar):
            for kw in _DEMAND_KEYWORDS:
                idx = max(1.0, level[t] + rng.normal(0, 3))
                change = (idx - prev[kw]) / prev[kw] * 100.0 if prev[kw] else 0.0
                rows.append(
                    {
                        "category_id": 1,
                        "weight": 1.0,
                        "keyword": kw,
                        "keyword_group": "DEMAND",
                        "observed_date": day.isoformat(),
                        "search_index": idx,
                        "change_pct": change,
                        "is_spike": change > 25.0,
                        "polarity": "demand",
                    }
                )
                prev[kw] = idx
            # Risk keywords: independent noise (so risk_momentum is a real, separate feature).
            for kw in _RISK_KEYWORDS:
                rows.append(
                    {
                        "category_id": 2,
                        "weight": 1.0,
                        "keyword": kw,
                        "keyword_group": "RISK",
                        "observed_date": day.isoformat(),
                        "search_index": max(1.0, rng.normal(20, 6)),
                        "change_pct": rng.normal(0, 10),
                        "is_spike": False,
                        "polarity": "risk",
                    }
                )
        datalab_rows_by_stock[stock_id] = rows
        signal_dates_by_stock[stock_id] = calendar[::signal_step]

    return datalab_rows_by_stock, prices_by_stock, signal_dates_by_stock, benchmark


def generate_magnitude_demo(
    *,
    n_stocks: int = 8,
    weeks: int = 140,
    start: date = date(2021, 1, 4),
    seed: int = 13,
    signal_step: int = 5,
):
    """Build ``(search_by_ticker, prices_by_ticker, signal_dates_by_ticker)`` for the
    MAGNITUDE task's offline demo — the shapes ``magnitude_dataset.build_magnitude_dataset``
    expects (prices carry volume).

    A learnable NON-directional relationship is planted: the daily return's
    *volatility* and the *volume* both rise with the recent search level, so a
    working pipeline lets a regressor predict forward realized-vol / abnormal-volume
    better than a mean baseline (the validated attention→magnitude survivor). It
    says nothing about real markets.
    """
    rng = np.random.default_rng(seed)
    calendar = _business_days(start, weeks * 5)
    n_days = len(calendar)

    search_by_ticker: dict[str, list[tuple[date, float]]] = {}
    prices_by_ticker: dict[str, PriceSeries] = {}
    signal_dates_by_ticker: dict[str, list[date]] = {}

    for k in range(n_stocks):
        ticker = f"M{k:03d}"
        level = np.empty(n_days)
        level[0] = 50.0
        for t in range(1, n_days):
            shock = rng.normal(0, 4) + (rng.random() < 0.04) * rng.normal(25, 8)
            level[t] = max(1.0, 0.92 * level[t - 1] + 0.08 * 50.0 + shock)

        closes = [100.0]
        volumes = [max(1.0, 1000.0 + rng.normal(0, 100))]
        for t in range(1, n_days):
            recent = level[max(0, t - 5):t].mean()
            # Higher search → higher return *dispersion* (magnitude), not drift.
            vol_scale = 0.5 + 0.04 * max(0.0, recent - 50.0)
            ret = rng.normal(0.0, vol_scale)
            closes.append(closes[-1] * (1.0 + ret / 100.0))
            volumes.append(max(1.0, 1000.0 * (1.0 + 0.03 * (level[t] - 50.0)) + rng.normal(0, 120)))

        prices_by_ticker[ticker] = PriceSeries.from_rows(
            list(zip(calendar, closes, volumes))
        )
        search_by_ticker[ticker] = [(d, float(level[t])) for t, d in enumerate(calendar)]
        signal_dates_by_ticker[ticker] = calendar[::signal_step]

    return search_by_ticker, prices_by_ticker, signal_dates_by_ticker


def generate_revenue_demo(
    *,
    n_stocks: int = 12,
    weeks: int = 416,
    start: date = date(2015, 1, 5),
    seed: int = 7,
    signal_step: int = 5,
    lag: int = 1,
    planted: bool = True,
):
    """Build ``(search_by_ticker, revenue_by_ticker, trading_days_by_ticker,
    signal_dates_by_ticker)`` for the REVENUE-nowcast task's offline demo — the
    shapes ``revenue_dataset.build_revenue_dataset`` expects.

    ``weeks=416`` ≈ 8 fiscal years so several annual prints exist. When ``planted``,
    each firm's search level is elevated in proportion to the CROSS-SECTIONAL YoY of
    its ``lag``-th upcoming revenue print — so the sweep's per-period (cross-
    sectional) IC should detect it and survive FDR. ``planted=False`` is the
    true-null control (search independent of revenue) — it must NOT survive.
    """
    from .revenue_dataset import _revenue_yoy_at_lag  # local: avoid import cycle

    rng = np.random.default_rng(seed)
    calendar = _business_days(start, weeks * 5)
    years = sorted({d.year for d in calendar})

    search_by_ticker: dict[str, list[tuple[date, float]]] = {}
    revenue_by_ticker: dict[str, list[tuple[int, float]]] = {}
    trading_days_by_ticker: dict[str, list[date]] = {}
    signal_dates_by_ticker: dict[str, list[date]] = {}

    for k in range(n_stocks):
        ticker = f"T{k:03d}"
        revlist: list[tuple[int, float]] = []
        prev = 1000.0 * (1.0 + 0.3 * rng.random())
        for year in years:
            growth = rng.normal(0.10, 0.30)  # cross-sectional YoY spread
            cur = max(1.0, prev * (1.0 + growth))
            revlist.append((year, cur))
            prev = cur
        revenue_by_ticker[ticker] = revlist

        series: list[tuple[date, float]] = []
        for d in calendar:
            signal = _revenue_yoy_at_lag(revlist, d, lag) if planted else math.nan
            bump = 30.0 * signal if (planted and math.isfinite(signal)) else 0.0
            series.append((d, max(1.0, 50.0 + bump + rng.normal(0, 8))))
        search_by_ticker[ticker] = series
        trading_days_by_ticker[ticker] = calendar
        signal_dates_by_ticker[ticker] = calendar[::signal_step]

    return (
        search_by_ticker,
        revenue_by_ticker,
        trading_days_by_ticker,
        signal_dates_by_ticker,
    )
