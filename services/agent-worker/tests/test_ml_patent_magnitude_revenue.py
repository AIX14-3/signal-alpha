"""특허 ML 확장 테스트 — 매그니튜드 라벨·매출 나우캐스팅·융합 k-of-n outer join.

전부 순수(DB/네트워크 없음). 누수 가드(publication 윈도·known_at)와 횡단면 이진화,
그리고 outer join의 결측→NaN 채움을 검증한다.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import numpy as np
import pytest

import app.ml.research.fusion_db as fusion_db
from app.ml.research.datalab_dataset import Dataset, PriceSeries
from app.ml.research.fundamentals_dataset import build_patent_revenue_dataset
from app.ml.research.patent_dataset import build_dataset


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _trading_days(start: date, n: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _patent_row(d: date) -> dict:
    return {
        "application_date": d,
        "publication_date": d,  # window + momentum on the publication timeline
        "is_new_category": False,
        "tech_category": "G06F",
        "significance": None,
    }


def _patent_multi(n_stocks: int, *, n_days: int = 80):
    days = _trading_days(date(2021, 1, 4), n_days)
    prices: dict[int, PriceSeries] = {}
    rows: dict[int, list[dict]] = {}
    for k in range(1, n_stocks + 1):
        slope = 0.3 * k  # distinct slopes -> distinct forward returns & vols
        closes = [100.0 + slope * i for i in range(n_days)]
        prices[k] = PriceSeries.from_pairs(list(zip(days, closes)))
        # patents PUBLISHED in the first 10 sessions -> known at the later signal dates
        rows[k] = [_patent_row(days[j]) for j in range(0, 10, 2)]
    signal_dates = days[12:16]
    sd = {k: signal_dates for k in range(1, n_stocks + 1)}
    return rows, prices, sd


# --------------------------------------------------------------------------- #
# WS1 — patent magnitude
# --------------------------------------------------------------------------- #
def test_patent_abs_return_binary_and_carries_magnitude():
    rows, prices, sd = _patent_multi(8)
    ds = build_dataset(
        patent_rows_by_stock=rows,
        prices_by_stock=prices,
        signal_dates_by_stock=sd,
        benchmark=None,
        lookback_days=60,
        horizon_sessions=5,
        min_observations=1,
        target="abs_return",
        min_cross_section=4,
    )
    assert len(ds) > 0
    assert set(ds.y.tolist()) <= {0, 1}
    assert ds.y.sum() == len(ds) // 2          # 8 stocks/date -> balanced 4/4
    assert (ds.excess_returns >= 0).all()      # |excess| is non-negative


def test_patent_realized_vol_target():
    rows, prices, sd = _patent_multi(8)
    ds = build_dataset(
        patent_rows_by_stock=rows,
        prices_by_stock=prices,
        signal_dates_by_stock=sd,
        benchmark=None,
        lookback_days=60,
        horizon_sessions=5,
        min_observations=1,
        target="realized_vol",
        min_cross_section=4,
    )
    assert len(ds) > 0
    assert set(ds.y.tolist()) <= {0, 1}
    assert (ds.excess_returns >= 0).all()      # realized vol non-negative


def test_patent_magnitude_thin_cross_section_drops_all():
    rows, prices, sd = _patent_multi(3)        # 3 stocks < min_cross_section=6
    ds = build_dataset(
        patent_rows_by_stock=rows,
        prices_by_stock=prices,
        signal_dates_by_stock=sd,
        benchmark=None,
        lookback_days=60,
        horizon_sessions=5,
        min_observations=1,
        target="abs_return",
        min_cross_section=6,
    )
    assert len(ds) == 0
    assert ds.dropped["thin_cross_section"] > 0


def test_patent_direction_target_unchanged_default():
    rows, prices, sd = _patent_multi(8)
    ds = build_dataset(
        patent_rows_by_stock=rows,
        prices_by_stock=prices,
        signal_dates_by_stock=sd,
        benchmark=None,
        lookback_days=60,
        horizon_sessions=5,
        min_observations=1,
    )  # default target="direction"
    assert len(ds) > 0
    assert set(ds.y.tolist()) <= {0, 1}        # up/down


def test_patent_unknown_target_raises():
    with pytest.raises(ValueError):
        build_dataset(
            patent_rows_by_stock={},
            prices_by_stock={},
            signal_dates_by_stock={},
            target="bogus",
        )


# --------------------------------------------------------------------------- #
# WS2 — patent -> next-quarter revenue nowcasting
# --------------------------------------------------------------------------- #
def _revenue_two_years(growth_factor: float) -> dict[tuple[int, int], tuple[float, str]]:
    """2021 base + 2022 = base*growth_factor; known_at ~45d after each quarter end."""
    base = {1: 100.0, 2: 110.0, 3: 120.0, 4: 130.0}
    known = {1: "2021-05-15", 2: "2021-08-15", 3: "2021-11-15", 4: "2022-03-15"}
    known22 = {1: "2022-05-15", 2: "2022-08-15", 3: "2022-11-15", 4: "2023-03-15"}
    out: dict[tuple[int, int], tuple[float, str]] = {}
    for q in (1, 2, 3, 4):
        out[(2021, q)] = (base[q], known[q])
        out[(2022, q)] = (base[q] * growth_factor, known22[q])
    return out


def _patents_spanning_2021_2022(stock_id: int) -> list[dict]:
    # one patent per month across 2021-2022 so every quarter-end lookback has filings
    rows = []
    for y in (2021, 2022):
        for m in range(1, 13):
            rows.append(_patent_row(date(y, m, 10)))
    return rows


def test_patent_revenue_builds_cross_sectional_growth_labels():
    n = 6
    patent_rows = {k: _patents_spanning_2021_2022(k) for k in range(1, n + 1)}
    # distinct YoY growth per stock so the per-quarter cross-section ranks cleanly
    revenue = {k: _revenue_two_years(1.0 + 0.05 * k) for k in range(1, n + 1)}
    ds = build_patent_revenue_dataset(
        patent_rows_by_stock=patent_rows,
        revenue_by_stock=revenue,
        lookback_days=365,
        min_observations=1,
        min_cross_section=4,
    )
    assert len(ds) > 0
    assert set(ds.y.tolist()) <= {0, 1}
    # YoY growth label only exists for 2022 (needs prior-year same quarter)
    assert len(np.unique(ds.dates)) <= 4       # at most 4 quarter-end dates (2022)
    # excess_returns carries continuous YoY growth (here all positive)
    assert (ds.excess_returns > 0).all()


def test_patent_revenue_leakage_guard_drops_known_before_asof():
    # revenue "announced" ON/BEFORE the quarter end (known_at <= as_of) must be dropped.
    leaky = {
        (2021, 1): (100.0, "2020-05-15"),
        (2022, 1): (120.0, "2022-03-31"),   # as_of = 2022-03-31, known_at == as_of -> leak
    }
    ds = build_patent_revenue_dataset(
        patent_rows_by_stock={1: _patents_spanning_2021_2022(1)},
        revenue_by_stock={1: leaky},
        lookback_days=365,
        min_observations=1,
        min_cross_section=1,
    )
    assert ds.dropped["leak_or_missing_known_at"] >= 1


# --------------------------------------------------------------------------- #
# WS3 — fusion k-of-n outer join
# --------------------------------------------------------------------------- #
def _ds_feat(keys: list[tuple[int, int]], feats: list[list[float]]) -> Dataset:
    n = len(keys)
    return Dataset(
        X=np.array(feats, dtype=float).reshape(n, len(feats[0]) if n else 0),
        y=np.ones(n, dtype=int),
        excess_returns=np.arange(n, dtype=float),
        dates=np.array([d for _, d in keys], dtype=int),
        stock_ids=np.array([s for s, _ in keys], dtype=int),
        feature_names=["f"],
        dropped={},
    )


def _run_fusion(monkeypatch, *, min_sources):
    per_source = {
        "patent": _ds_feat([(10, 100), (10, 101)], [[1.0], [2.0]]),
        "hiring": _ds_feat([(10, 100)], [[3.0]]),                 # missing (10,101)
        "datalab": _ds_feat([(10, 100), (10, 101)], [[4.0], [5.0]]),
    }

    async def _fake_load_one(source, **_kw):
        return per_source[source]

    monkeypatch.setattr(fusion_db, "_load_one", _fake_load_one)
    return asyncio.run(
        fusion_db.load_fusion(
            database_url="x", tickers=[], start=date(2021, 1, 1), end=date(2021, 1, 2),
            sources=["patent", "hiring", "datalab"], xs_normalize="none",
            min_sources=min_sources,
        )
    )


def test_fusion_inner_join_keeps_only_all_present(monkeypatch):
    ds = _run_fusion(monkeypatch, min_sources=None)   # None == inner (all 3)
    assert len(ds) == 1                                # only (10,100) is in all 3
    assert not np.isnan(ds.X).any()                    # inner -> no missing


def test_fusion_kofn_outer_fills_missing_with_nan(monkeypatch):
    ds = _run_fusion(monkeypatch, min_sources=2)       # keep keys in >= 2 sources
    assert len(ds) == 2                                # (10,100) in 3, (10,101) in 2
    # locate the (10,101) row: hiring column (index 1) must be NaN, others present
    rows_by_date = {int(d): ds.X[i] for i, d in enumerate(ds.dates)}
    assert np.isnan(rows_by_date[101][1])              # hiring missing -> NaN
    assert rows_by_date[101][0] == 2.0                 # patent present
    assert rows_by_date[101][2] == 5.0                 # datalab present
    assert ds.dropped["min_sources"] == 2
