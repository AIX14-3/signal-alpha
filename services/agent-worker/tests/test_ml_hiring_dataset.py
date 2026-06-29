"""Tests for the HIRING -> (X, y) dataset builder.

Hiring is sparse + strongly seasonal, so on top of the usual leakage guards we
assert the seasonality handling and the point-in-time feature windows:
- features at as_of use only postings observed ON OR BEFORE as_of (no future leak),
- forward-return labels read only FUTURE prices,
- the empirical seasonal index down-weights busy months,
- year-over-year is None (nan) without a prior-year window,
- too-few-observation / neutral samples are dropped and counted.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from app.ml.research.hiring_dataset import (
    build_dataset,
    hiring_features,
    seasonal_index,
)
from app.ml.research.datalab_dataset import PriceSeries


def _price_series(start: date, closes: list[float]) -> PriceSeries:
    days: list[date] = []
    d = start
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return PriceSeries.from_pairs(list(zip(days, closes)))


def _rows(dates: list[date]) -> list[dict]:
    return [{"observed_date": d.isoformat()} for d in dates]


# --- seasonality ------------------------------------------------------------

def test_seasonal_index_downweights_busy_months():
    # 10 postings in March, 1 each in Jan/Feb -> March factor well above 1.
    dates = [date(2020, 3, d) for d in range(1, 11)] + [date(2020, 1, 5), date(2020, 2, 5)]
    factors = seasonal_index(dates)
    assert factors[3] > 1.0           # 공채철-like peak month is busier than average
    assert factors[3] == max(factors.values())  # the busiest month has the largest factor
    assert factors[7] == 1.0          # empty month falls back to no-op (never 0)
    assert all(f > 0 for f in factors.values())  # no zero factor (would blow up a ratio)


def test_empty_seasonal_index_is_noop():
    assert seasonal_index([]) == {m: 1.0 for m in range(1, 13)}


# --- point-in-time features -------------------------------------------------

def test_features_ignore_future_postings():
    factors = {m: 1.0 for m in range(1, 13)}
    as_of = date(2021, 6, 30)
    # 3 postings before as_of (in the 90d window), 2 AFTER (must be invisible).
    before = [date(2021, 5, 10), date(2021, 6, 1), date(2021, 6, 20)]
    after = [date(2021, 7, 5), date(2021, 8, 1)]
    feats, n = hiring_features(
        sorted(before + after), as_of=as_of, lookback_days=90, factors=factors
    )
    assert n == 3  # only the 3 in-window, pre-as_of postings count
    # latest known posting is Jun 20 -> 10 days before as_of, never a July date.
    assert feats["hiring__days_since_latest"] == 10.0


def test_yoy_change_needs_prior_year_window():
    factors = {m: 1.0 for m in range(1, 13)}
    as_of = date(2021, 6, 30)
    # No postings ~1 year before -> yoy undefined (nan).
    feats, _ = hiring_features(
        [date(2021, 6, 1), date(2021, 6, 20)],
        as_of=as_of, lookback_days=90, factors=factors,
    )
    assert math.isnan(feats["hiring__yoy_change"])
    # Add a prior-year window posting -> yoy becomes finite.
    feats2, _ = hiring_features(
        [date(2020, 6, 1), date(2021, 6, 1), date(2021, 6, 20)],
        as_of=as_of, lookback_days=90, factors=factors,
    )
    assert not math.isnan(feats2["hiring__yoy_change"])


# --- dataset assembly / labels ---------------------------------------------

def test_label_direction_up_on_rising_prices():
    start = date(2021, 1, 4)
    prices = {1: _price_series(start, [100.0 + i for i in range(60)])}
    # Enough postings in the lookback window to clear min_observations.
    posts = [start + timedelta(days=k) for k in range(0, 40, 3)]
    ds = build_dataset(
        hiring_rows_by_stock={1: _rows(posts)},
        prices_by_stock=prices,
        signal_dates_by_stock={1: prices[1].dates[8:14]},
        benchmark=None,
        lookback_days=60,
        horizon_sessions=5,
        neutral_band_pct=0.1,
        min_observations=2,
    )
    assert len(ds) > 0
    assert set(ds.y.tolist()) == {1}
    assert ds.feature_names == [
        "hiring__days_since_latest",
        "hiring__deseason_momentum",
        "hiring__yoy_change",
    ]


def test_excess_return_subtracts_benchmark():
    start = date(2021, 1, 4)
    stock = _price_series(start, [100.0 + i for i in range(60)])      # rising
    bench = _price_series(start, [100.0 + 2 * i for i in range(60)])  # rising faster
    posts = [start + timedelta(days=k) for k in range(0, 40, 3)]
    ds = build_dataset(
        hiring_rows_by_stock={1: _rows(posts)},
        prices_by_stock={1: stock},
        signal_dates_by_stock={1: stock.dates[8:14]},
        benchmark=bench,
        lookback_days=60,
        horizon_sessions=5,
        neutral_band_pct=0.0,
        min_observations=2,
    )
    assert len(ds) > 0
    assert set(ds.y.tolist()) == {0}      # stock up but market up more -> excess down
    assert (ds.excess_returns < 0).all()


def test_too_few_observations_dropped():
    start = date(2021, 1, 4)
    prices = {1: _price_series(start, [100.0 + i for i in range(40)])}
    ds = build_dataset(
        hiring_rows_by_stock={1: []},  # no postings at all
        prices_by_stock=prices,
        signal_dates_by_stock={1: prices[1].dates[:5]},
        min_observations=2,
    )
    assert len(ds) == 0
    assert ds.dropped["too_few_observations"] == 5


def test_no_price_series_dropped():
    ds = build_dataset(
        hiring_rows_by_stock={1: _rows([date(2021, 1, 5)])},
        prices_by_stock={},  # stock 1 has no price series
        signal_dates_by_stock={1: [date(2021, 1, 10), date(2021, 1, 17)]},
        min_observations=1,
    )
    assert len(ds) == 0
    assert ds.dropped["no_price_series"] == 2
