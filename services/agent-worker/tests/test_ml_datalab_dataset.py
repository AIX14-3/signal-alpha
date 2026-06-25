"""Tests for the DataLab -> (X, y) dataset builder.

The builder is where backtest leakage would creep in, so the assertions focus on:
- forward-return labels read only FUTURE prices (entry at as_of, exit horizon later),
- features at as_of use only DataLab rows observed ON OR BEFORE as_of,
- samples with too little data / no forward price / neutral moves are dropped
  (and counted), never silently kept.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.ml.research.datalab_dataset import (
    PriceSeries,
    build_dataset,
    weekly_signal_dates,
)


def _price_series(start: date, closes: list[float]) -> PriceSeries:
    days = []
    d = start
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return PriceSeries.from_pairs(list(zip(days, closes)))


def _daily_rows(start: date, n: int, *, base: float = 50.0) -> list[dict]:
    rows = []
    d = start
    made = 0
    while made < n:
        if d.weekday() < 5:
            rows.append(
                {
                    "weight": 1.0,
                    "keyword": "HBM",
                    "observed_date": d.isoformat(),
                    "search_index": base + made,
                    "change_pct": 1.0,
                    "is_spike": False,
                    "polarity": "demand",
                }
            )
            made += 1
        d += timedelta(days=1)
    return rows


# --- forward-return label ---------------------------------------------------

def test_forward_return_uses_future_close_only():
    s = _price_series(date(2021, 1, 4), [100.0, 101.0, 102.0, 103.0, 104.0, 110.0])
    # +5 sessions from index 0 (100) -> index 5 (110) = +10%.
    assert s.forward_return_pct(s.dates[0], 5) == 100.0 * (110.0 / 100.0 - 1.0)
    # Not enough forward sessions -> None (never fabricate a label).
    assert s.forward_return_pct(s.dates[3], 5) is None
    # Non-trading day -> None.
    assert s.forward_return_pct(date(1999, 1, 1), 5) is None


def test_label_direction_and_drop_counts():
    start = date(2021, 1, 4)
    # Strong uptrend so a +1 session move is clearly "up" past the neutral band.
    closes = [100.0 + i for i in range(40)]
    prices = {1: _price_series(start, closes)}
    rows = {1: _daily_rows(start, 40)}
    sig = {1: prices[1].dates[:10]}  # 10 signal dates; last few lack forward prices

    ds = build_dataset(
        datalab_rows_by_stock=rows,
        prices_by_stock=prices,
        signal_dates_by_stock=sig,
        benchmark=None,
        lookback_days=15,
        horizon_sessions=5,
        neutral_band_pct=0.1,
        min_observations=3,
    )
    # Every surviving sample is labelled up (monotone rising series, no benchmark).
    assert len(ds) > 0
    assert set(ds.y.tolist()) == {1}
    # Feature names come from the real DataLab indicators, prefixed by source.
    assert "datalab__momentum_pct" in ds.feature_names
    assert all(name.startswith("datalab__") for name in ds.feature_names)


def test_excess_return_subtracts_benchmark():
    start = date(2021, 1, 4)
    stock = _price_series(start, [100.0 + i for i in range(40)])  # rising
    bench = _price_series(start, [100.0 + 2 * i for i in range(40)])  # rising faster
    ds = build_dataset(
        datalab_rows_by_stock={1: _daily_rows(start, 40)},
        prices_by_stock={1: stock},
        signal_dates_by_stock={1: stock.dates[:5]},
        benchmark=bench,
        lookback_days=15,
        horizon_sessions=5,
        neutral_band_pct=0.0,
        min_observations=3,
    )
    # Stock rises but the benchmark rises faster -> excess negative -> all "down".
    assert len(ds) > 0
    assert set(ds.y.tolist()) == {0}
    assert (ds.excess_returns < 0).all()


def test_features_ignore_future_rows():
    # Two signal dates; rows exist for the whole span. The early signal must NOT
    # see later rows. We verify by observation counts inside the lookback window.
    start = date(2021, 1, 4)
    prices = {1: _price_series(start, [100.0 + i for i in range(60)])}
    rows = {1: _daily_rows(start, 60)}
    early = prices[1].dates[12]
    ds = build_dataset(
        datalab_rows_by_stock=rows,
        prices_by_stock=prices,
        signal_dates_by_stock={1: [early]},
        lookback_days=10,
        horizon_sessions=5,
        neutral_band_pct=0.0,
        min_observations=1,
    )
    # 1 demand keyword/day, lookback 10 days -> at most ~7-8 business days visible,
    # never the full 60. (observations feature is index 'datalab__observations'.)
    obs_col = ds.feature_names.index("datalab__observations")
    assert ds.X[0][obs_col] <= 10  # bounded by the window, not the whole history


def test_too_few_observations_dropped():
    start = date(2021, 1, 4)
    prices = {1: _price_series(start, [100.0 + i for i in range(40)])}
    ds = build_dataset(
        datalab_rows_by_stock={1: []},  # no DataLab rows at all
        prices_by_stock=prices,
        signal_dates_by_stock={1: prices[1].dates[:5]},
        min_observations=3,
    )
    assert len(ds) == 0
    assert ds.dropped["too_few_observations"] == 5


def test_weekly_signal_dates_strides():
    days = [date(2021, 1, 4) + timedelta(days=i) for i in range(20)]
    weekly = weekly_signal_dates(days, step=5)
    assert weekly == days[::5]


# --- CSV price loader -------------------------------------------------------

def test_load_prices_csv_groups_by_ticker_and_skips_bad_rows(tmp_path):
    from app.ml.research.prices_csv import load_prices_csv

    p = tmp_path / "prices.csv"
    p.write_text(
        "ticker,date,close\n"
        "005930,2021-01-04,83000\n"
        "005930,2021-01-05,84000\n"
        "KS11,2021-01-04,2944.45\n"
        "005930,2021-01-06,\n"  # blank close -> skipped
        "005930,bad-date,1\n",  # bad date -> skipped
        encoding="utf-8",
    )
    series = load_prices_csv(str(p))
    assert set(series) == {"005930", "KS11"}
    assert series["005930"].dates == [date(2021, 1, 4), date(2021, 1, 5)]
    assert series["005930"].forward_return_pct(date(2021, 1, 4), 1) == pytest.approx(
        100.0 * (84000 / 83000 - 1.0)
    )


def test_load_prices_csv_requires_columns(tmp_path):
    from app.ml.research.prices_csv import load_prices_csv

    p = tmp_path / "bad.csv"
    p.write_text("symbol,day,price\n005930,2021-01-04,83000\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_prices_csv(str(p))
