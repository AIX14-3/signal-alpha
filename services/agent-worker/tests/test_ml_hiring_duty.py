"""Unit tests for the duty-mix (tech-share) hiring features."""
from __future__ import annotations

import math
from datetime import date

from app.ml.research.hiring_dataset import (
    DUTY_FEATURES,
    VOLUME_FEATURES,
    build_dataset,
    duty_features,
    duty_tally,
)
from app.ml.research.datalab_dataset import PriceSeries


def test_duty_tally_counts_tech_vs_total():
    names = ["서버·백엔드개발", "마케팅", "데이터분석", "기타"]  # 2 tech of 4
    tech, total = duty_tally(names)
    assert (tech, total) == (2, 4)
    assert duty_tally(None) == (0, 0)
    assert duty_tally([]) == (0, 0)
    # dedups defensively
    assert duty_tally(["웹개발", "웹개발", "마케팅"]) == (1, 2)


def test_duty_features_pointwise_and_leakage():
    as_of = date(2022, 6, 30)
    # within lookback (90d): one all-tech posting, one no-tech posting -> share 0.5
    postings = [
        (date(2022, 5, 1), 2, 2),   # in window, all tech
        (date(2022, 6, 1), 0, 2),   # in window, no tech
        (date(2022, 8, 1), 2, 2),   # AFTER as_of -> must be ignored (leakage guard)
    ]
    f = duty_features(postings, as_of=as_of, lookback_days=90)
    # pooled tech share = (2+0)/(2+2) = 0.5
    assert math.isclose(f["hiring__tech_share"], 0.5)
    # the future posting must not leak in
    assert f["hiring__tech_share"] != 1.0


def test_duty_features_yoy_and_mom():
    as_of = date(2022, 6, 30)
    postings = [
        # one year earlier window -> tech share 0.0
        (date(2021, 6, 1), 0, 2),
        # current window, prior half (older) low tech, recent half high tech
        (date(2022, 4, 5), 0, 2),   # prior half
        (date(2022, 6, 20), 2, 2),  # recent half
    ]
    f = duty_features(postings, as_of=as_of, lookback_days=90)
    # current pooled share = (0+2)/(2+2)=0.5 ; prev-year=0.0 -> yoy=+0.5
    assert math.isclose(f["hiring__tech_share_yoy"], 0.5)
    # recent half share 1.0, prior half 0.0 -> mom = +1.0
    assert math.isclose(f["hiring__tech_share_mom"], 1.0)


def test_duty_features_nan_when_empty():
    f = duty_features([], as_of=date(2022, 1, 1), lookback_days=90)
    assert all(math.isnan(v) for v in f.values())


def _prices(start=date(2022, 1, 1), n=400):
    from datetime import timedelta
    pairs = [(start + timedelta(days=i), 100.0 + i * 0.1) for i in range(n)]  # rising
    return PriceSeries.from_pairs(pairs)


def test_build_dataset_feature_set_selection():
    rows = [
        {"observed_date": date(2022, m, 1),
         "duty_groups": ["서버·백엔드개발", "마케팅"]}
        for m in range(1, 7)
    ]
    hiring = {1: rows}
    prices = {1: _prices()}
    sig = {1: [date(2022, 5, 2), date(2022, 6, 2)]}

    common = dict(
        hiring_rows_by_stock=hiring, prices_by_stock=prices,
        signal_dates_by_stock=sig, lookback_days=120, horizon_sessions=5,
        neutral_band_pct=0.0, min_observations=1,
    )
    vol = build_dataset(**common, feature_set="volume")
    duty = build_dataset(**common, feature_set="duty")
    both = build_dataset(**common, feature_set="volume+duty")

    assert set(vol.feature_names) == set(VOLUME_FEATURES)
    assert set(duty.feature_names) == set(DUTY_FEATURES)
    assert set(both.feature_names) == set(VOLUME_FEATURES) | set(DUTY_FEATURES)


def test_build_dataset_duty_without_duty_groups_is_safe():
    # rows lacking duty_groups -> tallies are (0,0) -> NaN share, no crash
    rows = [{"observed_date": date(2022, m, 1)} for m in range(1, 7)]
    ds = build_dataset(
        hiring_rows_by_stock={1: rows}, prices_by_stock={1: _prices()},
        signal_dates_by_stock={1: [date(2022, 6, 2)]},
        lookback_days=120, horizon_sessions=5, neutral_band_pct=0.0,
        min_observations=1, feature_set="duty",
    )
    assert set(ds.feature_names) == set(DUTY_FEATURES)
