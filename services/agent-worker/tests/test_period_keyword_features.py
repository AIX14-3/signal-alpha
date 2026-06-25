"""Unit tests for the period-keyword feature adapter + dataset builder (Stage 5).

The non-negotiable property is the point-in-time gate: a keyword must not enter
any feature before its ``first_avail_date``. The ``fixed_keyword`` control must
NOT gate, so the same late keyword is visible from the start — that contrast is
the whole experiment, so it gets a direct test.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.ml.datalab_dataset import PriceSeries
from app.ml.period_keyword_dataset import (
    build_period_keyword_dataset,
    keyword_feature_row,
    load_keyword_meta,
    load_keyword_series,
)


def _series():
    # Two keywords for one ticker; "neuromorphic" only becomes available in 2018.
    weeks = [date(2017, 1, 2) + timedelta(weeks=i) for i in range(200)]  # ~2017..2020
    semi = [(w, 50.0 + i % 5) for i, w in enumerate(weeks)]          # steady
    neuro = [(w, float(10 * (i % 7))) for i, w in enumerate(weeks)]  # spiky
    return {
        ("000660", "반도체"): semi,
        ("000660", "뉴로모픽"): neuro,
    }


def _first_avail():
    return {
        ("000660", "반도체"): date(2017, 1, 4),
        ("000660", "뉴로모픽"): date(2018, 3, 1),  # not knowable before this
    }


def test_pit_gate_hides_keyword_before_first_avail():
    series, first = _series(), _first_avail()
    # as_of in 2017: only the 2017-available keyword should be active.
    row = keyword_feature_row(
        ticker="000660",
        as_of=date(2017, 6, 1),
        series_by_kw=series,
        first_avail=first,
        lookback_days=30,
        pit_gate=True,
    )
    assert row is not None
    assert row["period_keyword__n_active"] == 1.0

    # as_of in 2019: both keywords are now available.
    row_later = keyword_feature_row(
        ticker="000660",
        as_of=date(2019, 6, 1),
        series_by_kw=series,
        first_avail=first,
        lookback_days=30,
        pit_gate=True,
    )
    assert row_later["period_keyword__n_active"] == 2.0


def test_fixed_mode_does_not_gate_so_late_keyword_is_visible_early():
    series, first = _series(), _first_avail()
    row = keyword_feature_row(
        ticker="000660",
        as_of=date(2017, 6, 1),
        series_by_kw=series,
        first_avail=first,
        lookback_days=30,
        pit_gate=False,  # control: no point-in-time gate
    )
    # Both keywords visible in 2017 even though "뉴로모픽" patent is from 2018.
    assert row["period_keyword__n_active"] == 2.0


def test_no_active_keyword_returns_none():
    series, first = _series(), _first_avail()
    # Window before any data exists -> nothing in the lookback window.
    row = keyword_feature_row(
        ticker="000660",
        as_of=date(2015, 1, 1),
        series_by_kw=series,
        first_avail=first,
        lookback_days=30,
        pit_gate=True,
    )
    assert row is None


def test_features_are_set_invariant_keys():
    series, first = _series(), _first_avail()
    row = keyword_feature_row(
        ticker="000660",
        as_of=date(2019, 6, 1),
        series_by_kw=series,
        first_avail=first,
        lookback_days=30,
        pit_gate=True,
    )
    # The schema must be the same fixed aggregate set regardless of active count.
    assert set(row) == {
        "period_keyword__n_active",
        "period_keyword__mean_level",
        "period_keyword__mean_momentum",
        "period_keyword__max_momentum",
        "period_keyword__breadth",
        "period_keyword__spike_count",
    }


def test_build_dataset_label_uses_only_future_price_and_drops_neutral():
    series, first = _series(), _first_avail()
    # Prices: rising so forward return is clearly positive (not neutral).
    pdates = [date(2019, 1, 7) + timedelta(days=i) for i in range(40)]
    prices = PriceSeries.from_pairs([(d, 100.0 + i) for i, d in enumerate(pdates)])
    signal_dates = {"000660": [date(2019, 1, 7), date(2019, 1, 14)]}
    ds = build_period_keyword_dataset(
        series_by_kw=series,
        first_avail=first,
        prices_by_ticker={"000660": prices},
        signal_dates_by_ticker=signal_dates,
        benchmark=None,
        feature_mode="period_keyword",
        lookback_days=30,
        horizon_sessions=3,
        neutral_band_pct=0.3,
    )
    assert len(ds) >= 1
    # Rising price over horizon -> direction up (1).
    assert set(ds.y.tolist()) <= {0, 1}
    assert ds.y.sum() >= 1


def test_unknown_feature_mode_raises():
    try:
        build_period_keyword_dataset(
            series_by_kw={},
            first_avail={},
            prices_by_ticker={},
            signal_dates_by_ticker={},
            feature_mode="bogus",
        )
    except ValueError as exc:
        assert "feature_mode" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for bad feature_mode")


def test_loaders_round_trip(tmp_path):
    csv_path = tmp_path / "kw.csv"
    csv_path.write_text(
        "ticker,keyword,period,ratio\n"
        "000660,반도체,2017-01-02,50.0\n"
        "000660,반도체,2017-01-09,55.0\n",
        encoding="utf-8",
    )
    meta_path = tmp_path / "meta.json"
    meta_path.write_text(
        '[{"ticker":"000660","keyword":"반도체","first_avail_date":"2017-01-04"},'
        '{"ticker":"000660","keyword":"반도체","first_avail_date":"2016-12-30"}]',
        encoding="utf-8",
    )
    series = load_keyword_series(str(csv_path))
    meta = load_keyword_meta([str(meta_path)])
    assert series[("000660", "반도체")] == [
        (date(2017, 1, 2), 50.0),
        (date(2017, 1, 9), 55.0),
    ]
    # EARLIEST first_avail_date wins across duplicate meta entries.
    assert meta[("000660", "반도체")] == date(2016, 12, 30)
