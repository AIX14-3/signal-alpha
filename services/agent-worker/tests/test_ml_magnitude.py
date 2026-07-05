"""매그니튜드(움직임 크기) 타깃 테스트.

- forward_realized_vol: forward 구간 일간수익률 std, forward 부족/비거래일 → None
- abs_excess: |초과수익|
- cross_sectional_median_labels: per-date 상/하위 절반, 홀수 중앙 드롭, 빈약 날짜 드롭
- build_dataset(target=abs_return|realized_vol): y 이진·excess=연속매그니튜드·횡단면 처리
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

import pytest

from app.ml.research.datalab_dataset import PriceSeries
from app.ml.research.hiring_dataset import build_dataset
from app.ml.research.magnitude import (
    abs_excess,
    cross_sectional_median_labels,
    forward_realized_vol,
)


def _series(start: date, closes: list[float]) -> PriceSeries:
    days: list[date] = []
    d = start
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return PriceSeries.from_pairs(list(zip(days, closes)))


# --- forward_realized_vol ----------------------------------------------------

def test_forward_realized_vol_matches_manual_std():
    closes = [100.0, 110.0, 99.0, 108.9]  # as_of=idx0, horizon=3
    ps = _series(date(2021, 1, 4), closes)
    as_of = ps.dates[0]
    rets = [(110 / 100 - 1) * 100, (99 / 110 - 1) * 100, (108.9 / 99 - 1) * 100]
    expected = statistics.stdev(rets)  # 표본표준편차(ddof=1)
    got = forward_realized_vol(ps, as_of, 3)
    assert got is not None
    assert math.isclose(got, expected, rel_tol=1e-9)


def test_forward_realized_vol_none_when_insufficient_forward():
    ps = _series(date(2021, 1, 4), [100.0, 101.0, 102.0])
    # as_of=last index -> no forward sessions
    assert forward_realized_vol(ps, ps.dates[-1], 3) is None
    # horizon longer than remaining
    assert forward_realized_vol(ps, ps.dates[0], 5) is None


def test_forward_realized_vol_none_for_non_trading_day():
    ps = _series(date(2021, 1, 4), [100.0, 101.0, 102.0, 103.0])
    assert forward_realized_vol(ps, date(1999, 1, 1), 2) is None


# --- abs_excess --------------------------------------------------------------

def test_abs_excess_is_absolute_value():
    assert abs_excess(5.0, 2.0) == 3.0     # +3 excess
    assert abs_excess(1.0, 4.0) == 3.0     # -3 excess -> abs 3
    assert abs_excess(3.0, 3.0) == 0.0


# --- cross_sectional_median_labels -------------------------------------------

def test_even_date_splits_bottom_zero_top_one():
    mags = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    dates = [10] * 6
    keep, labels = cross_sectional_median_labels(mags, dates, min_cross_section=4)
    by = dict(zip(keep, labels))
    assert len(keep) == 6
    # bottom 3 mags (idx 0,1,2) -> 0 ; top 3 (3,4,5) -> 1
    assert {by[0], by[1], by[2]} == {0}
    assert {by[3], by[4], by[5]} == {1}
    assert sum(labels) == 3


def test_odd_date_drops_middle():
    mags = [1.0, 2.0, 3.0, 4.0, 5.0]  # n=5 -> drop median (3.0)
    dates = [10] * 5
    keep, labels = cross_sectional_median_labels(mags, dates, min_cross_section=4)
    assert len(keep) == 4
    assert 2 not in keep            # the median index (mag 3.0) dropped
    assert sorted(labels) == [0, 0, 1, 1]


def test_thin_date_dropped_entirely():
    mags = [1.0, 2.0, 3.0]
    dates = [10] * 3
    keep, labels = cross_sectional_median_labels(mags, dates, min_cross_section=6)
    assert keep == [] and labels == []


def test_multiple_dates_labeled_independently():
    mags = [1.0, 9.0, 2.0, 8.0, 3.0, 7.0]   # date 10: idx0,2,4 ; date 20: idx1,3,5
    dates = [10, 20, 10, 20, 10, 20]
    keep, labels = cross_sectional_median_labels(mags, dates, min_cross_section=2)
    by = dict(zip(keep, labels))
    # within date 10 (mags 1,2,3 at idx0,2,4): top1=idx4 ; n=3 -> drop middle idx2
    assert by[4] == 1 and by[0] == 0 and 2 not in by
    # within date 20 (mags 9,8,7 at idx1,3,5): top1=idx1 ; drop middle idx3
    assert by[1] == 1 and by[5] == 0 and 3 not in by


# --- build_dataset magnitude integration -------------------------------------

def _multi(n_stocks: int, *, n_days: int = 80):
    start = date(2021, 1, 4)
    days: list[date] = []
    d = start
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    prices: dict[int, PriceSeries] = {}
    rows: dict[int, list[dict]] = {}
    for k in range(1, n_stocks + 1):
        slope = 0.3 * k  # distinct slopes -> distinct forward returns & vols
        closes = [100.0 + slope * i for i in range(n_days)]
        prices[k] = PriceSeries.from_pairs(list(zip(days, closes)))
        posts = [days[0] + timedelta(days=j) for j in range(0, 30, 3)]
        rows[k] = [{"observed_date": p.isoformat()} for p in posts]
    signal_dates = days[12:16]  # shared; forward horizon available
    sd = {k: signal_dates for k in range(1, n_stocks + 1)}
    return rows, prices, sd


def test_build_dataset_abs_return_binary_and_carries_magnitude():
    rows, prices, sd = _multi(8)
    ds = build_dataset(
        hiring_rows_by_stock=rows,
        prices_by_stock=prices,
        signal_dates_by_stock=sd,
        benchmark=None,
        lookback_days=60,
        horizon_sessions=5,
        min_observations=2,
        target="abs_return",
        min_cross_section=4,
    )
    assert len(ds) > 0
    assert set(ds.y.tolist()) <= {0, 1}
    # 8 stocks/date (even) -> balanced 4/4 per date
    assert ds.y.sum() == len(ds) // 2
    # excess_returns carries the continuous magnitude = |excess| >= 0
    assert (ds.excess_returns >= 0).all()


def test_build_dataset_realized_vol_target():
    rows, prices, sd = _multi(8)
    ds = build_dataset(
        hiring_rows_by_stock=rows,
        prices_by_stock=prices,
        signal_dates_by_stock=sd,
        benchmark=None,
        lookback_days=60,
        horizon_sessions=5,
        min_observations=2,
        target="realized_vol",
        min_cross_section=4,
    )
    assert len(ds) > 0
    assert set(ds.y.tolist()) <= {0, 1}
    assert (ds.excess_returns >= 0).all()  # realized vol is non-negative


def test_build_dataset_thin_cross_section_drops_all():
    rows, prices, sd = _multi(3)  # only 3 stocks < min_cross_section=6
    ds = build_dataset(
        hiring_rows_by_stock=rows,
        prices_by_stock=prices,
        signal_dates_by_stock=sd,
        benchmark=None,
        lookback_days=60,
        horizon_sessions=5,
        min_observations=2,
        target="abs_return",
        min_cross_section=6,
    )
    assert len(ds) == 0
    assert ds.dropped["thin_cross_section"] > 0


def test_unknown_target_raises():
    with pytest.raises(ValueError):
        build_dataset(
            hiring_rows_by_stock={},
            prices_by_stock={},
            signal_dates_by_stock={},
            target="bogus",
        )
