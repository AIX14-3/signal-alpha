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
