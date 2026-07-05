"""portfolio.py — 수익률 시계열 백테스트 계층 테스트.

수익률 구성(daily/basket/period 복리) · 지표(Sharpe·t-stat·skew/kurt·PSR·**DSR 디플레이션**) ·
오버레이(vol_managed 무조건 vol 매칭·무타이밍 불변·타이밍 개선) · 가중(inverse_vol) · 결정론.
전부 손수 만든 PriceSeries — DB·clock·RNG 없음.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

import numpy as np

from app.ml.research.datalab_dataset import PriceSeries
from app.ml.research.portfolio import (
    ArmResult,
    annualized_sharpe,
    constituent_period_returns,
    daily_returns,
    deflated_sharpe_ratio,
    equal_weight_basket,
    equal_weight_period_returns,
    expected_max_sharpe,
    inverse_vol_weights,
    period_returns,
    probabilistic_sharpe_ratio,
    sharpe_report,
    sharpe_tstat,
    skew_kurt,
    trailing_realized_variance,
    vol_managed,
)


def _series(start: date, closes: list[float]) -> PriceSeries:
    """평일만 이어붙인 종가 시계열(test_ml_magnitude 패턴)."""
    days: list[date] = []
    d = start
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return PriceSeries.from_pairs(list(zip(days, closes)))


# --- 수익률 구성 -------------------------------------------------------------
def test_daily_returns_match_close_ratios_and_skip_first():
    ps = _series(date(2021, 1, 4), [100.0, 110.0, 99.0])
    dr = daily_returns(ps)
    assert ps.dates[0] not in dr  # 첫날은 수익률 없음
    assert math.isclose(dr[ps.dates[1]], 0.10)
    assert math.isclose(dr[ps.dates[2]], 99.0 / 110.0 - 1.0)


def test_equal_weight_basket_is_mean_over_available_constituents():
    a = _series(date(2021, 1, 4), [100.0, 110.0, 121.0])  # +10%, +10%
    b = _series(date(2021, 1, 4), [100.0, 100.0, 120.0])  # 0%, +20%
    basket = equal_weight_basket({"A": a, "B": b}, ["A", "B"])
    d1, d2 = a.dates[1], a.dates[2]
    assert math.isclose(basket[d1], (0.10 + 0.0) / 2)
    assert math.isclose(basket[d2], (0.10 + 0.20) / 2)


def test_equal_weight_basket_uses_only_present_constituent_on_a_date():
    a = _series(date(2021, 1, 4), [100.0, 110.0])
    b = _series(date(2021, 1, 5), [100.0, 130.0])  # a보다 하루 뒤 시작
    basket = equal_weight_basket({"A": a, "B": b}, ["A", "B"])
    # a의 유일 수익일에는 a만 존재 → a값. 날조하지 않음.
    assert math.isclose(basket[a.dates[1]], 0.10)


def test_period_returns_compound_within_span():
    # 두 +10%일 → 한 기간 21%
    ps = _series(date(2021, 1, 4), [100.0, 110.0, 121.0, 121.0])
    dr = daily_returns(ps)
    rebals = [ps.dates[0], ps.dates[2], ps.dates[3]]  # (d0,d2] then (d2,d3]
    ends, arr = period_returns(dr, rebals)
    assert ends == [ps.dates[2], ps.dates[3]]
    assert math.isclose(arr[0], 0.21)
    assert math.isclose(arr[1], 0.0)


def test_period_returns_nan_when_no_trading_in_span():
    ps = _series(date(2021, 1, 4), [100.0, 110.0])
    dr = daily_returns(ps)
    far = ps.dates[1] + timedelta(days=30)
    _, arr = period_returns(dr, [ps.dates[1], far])  # (d1, far] 에 수익일 없음
    assert math.isnan(arr[0])


def test_equal_weight_period_returns_average_ignoring_nan():
    a = _series(date(2021, 1, 4), [100.0, 110.0, 121.0])
    b = _series(date(2021, 1, 4), [100.0, 100.0, 120.0])
    rebals = [a.dates[0], a.dates[2]]
    cpr = constituent_period_returns({"A": a, "B": b}, ["A", "B"], rebals)
    ew = equal_weight_period_returns(cpr, ["A", "B"])
    # A: (121/100-1)=0.21, B: (120/100-1)=0.20 → mean 0.205
    assert math.isclose(ew[0], 0.205)


# --- 지표 --------------------------------------------------------------------
def test_annualized_sharpe_hand_value():
    r = np.array([0.01, 0.02, 0.03, 0.02, 0.01, 0.03])
    sd = r.std(ddof=1)
    expected = r.mean() / sd * math.sqrt(12.0)
    assert math.isclose(annualized_sharpe(r, 12.0), expected)


def test_sharpe_tstat_scales_with_sqrt_n_and_flips_sign():
    r = np.array([0.01, -0.005, 0.02, 0.015, 0.0, 0.01])
    sd = r.std(ddof=1)
    assert math.isclose(sharpe_tstat(r), r.mean() / sd * math.sqrt(r.size))
    assert math.isclose(sharpe_tstat(-r), -sharpe_tstat(r))


def test_skew_kurt_is_non_excess_kurtosis():
    # 대칭 → 왜도≈0; 첨도는 **비초과**(정규≈3, fisher=True의 ≈0이 아님)
    rng = [math.sin(i) for i in range(400)]
    g3, g4 = skew_kurt(np.array(rng))
    assert abs(g3) < 0.1
    assert g4 > 1.0  # 비초과 첨도(양수·수 단위). fisher=True였다면 음수(≈-1.5)일 것
    assert not (-0.5 < g4 < 0.5)  # 초과첨도(≈0) 형태가 아님을 회귀가드


def test_psr_monotonic_in_n_and_half_at_own_sr():
    r = np.array([0.02, 0.01, 0.03, 0.015, 0.025, 0.005, 0.02, 0.01])
    sd = r.std(ddof=1)
    sr_hat = r.mean() / sd
    # SR* = SR̂ → PSR ≈ 0.5
    assert abs(probabilistic_sharpe_ratio(r, sr_hat) - 0.5) < 1e-6
    # 표본이 커지면(같은 분포 반복) PSR 증가
    small = probabilistic_sharpe_ratio(r, 0.0)
    big = probabilistic_sharpe_ratio(np.tile(r, 4), 0.0)
    assert big > small


def test_deflated_sharpe_shrinks_with_more_trials():
    r = np.array([0.02, 0.01, 0.03, 0.015, 0.025, 0.005, 0.02, 0.01, 0.018, 0.012])
    d1 = deflated_sharpe_ratio(r, n_trials=1)
    d10 = deflated_sharpe_ratio(r, n_trials=10)
    d1000 = deflated_sharpe_ratio(r, n_trials=1000)
    assert d1 > d10 > d1000  # 핵심 디플레이션 성질


def test_expected_max_sharpe_increases_in_trials_and_zero_var():
    assert expected_max_sharpe(0.0, 100) == 0.0
    assert expected_max_sharpe(0.01, 1000) > expected_max_sharpe(0.01, 10)


# --- 오버레이 / 가중 ---------------------------------------------------------
def test_vol_managed_matches_base_unconditional_vol():
    base = np.array([0.02, -0.01, 0.03, -0.02, 0.01, 0.015, -0.005, 0.02])
    vf = np.array([1.0, 4.0, 0.25, 2.0, 1.5, 0.5, 3.0, 1.0])
    managed = vol_managed(base, vf)
    assert math.isclose(managed.std(ddof=1), base.std(ddof=1), rel_tol=1e-9)


def test_vol_managed_constant_forecast_reproduces_base():
    base = np.array([0.02, -0.01, 0.03, -0.02, 0.01, 0.015])
    managed = vol_managed(base, np.full(base.shape, 2.0))
    assert np.allclose(managed, base)  # 상수 예측 → 스케일 균일 → base 재현(무타이밍)


def test_vol_managed_improves_sharpe_with_genuine_vol_clustering():
    # 고변동 기간이 낮은(음의) 평균수익을 동반 → vol-타이밍이 샤프 개선(메커니즘 sanity)
    calm = [0.02, 0.018, 0.022, 0.02, 0.019, 0.021]
    turbulent = [-0.05, 0.06, -0.055, 0.05, -0.045, 0.04]
    base = np.array(calm + turbulent)
    # t-1 기지 예측: calm 기간엔 낮은 분산, turbulent 기간엔 높은 분산
    vf = np.array([0.0004] * 6 + [0.0025] * 6)
    managed = vol_managed(base, vf)
    assert annualized_sharpe(managed, 12.0) > annualized_sharpe(base, 12.0)


def test_inverse_vol_weights_favor_low_vol_and_sum_to_one():
    w = inverse_vol_weights({"A": 1.0, "B": 2.0, "C": 4.0})
    assert math.isclose(sum(w.values()), 1.0)
    assert w["A"] > w["B"] > w["C"]  # 저변동에 더 큰 가중


def test_inverse_vol_weights_degenerate_to_equal():
    # 유효 점수 <2 → equal-weight fallback
    w = inverse_vol_weights({"A": float("nan"), "B": -1.0, "C": 3.0})
    assert all(math.isclose(v, 1 / 3) for v in w.values())
    eq = inverse_vol_weights({"A": 5.0, "B": 5.0})
    assert math.isclose(eq["A"], 0.5) and math.isclose(eq["B"], 0.5)


def test_trailing_realized_variance_matches_statistics_and_guards():
    ps = _series(date(2021, 1, 4), [100, 110, 99, 108.9, 100])
    dr = daily_returns(ps)
    as_of = ps.dates[-1]
    got = trailing_realized_variance(dr, as_of, lookback=4)
    assert math.isclose(got, statistics.variance(list(dr.values())))
    # 창 부족 → None
    assert trailing_realized_variance(dr, ps.dates[0], lookback=4) is None


# --- 결정론 ------------------------------------------------------------------
def test_sharpe_report_is_deterministic():
    r = np.array([0.02, 0.01, 0.03, 0.015, 0.025, 0.005, 0.02, 0.01])
    a = sharpe_report(r, name="x", periods_per_year=12.0, n_trials=5)
    b = sharpe_report(r, name="x", periods_per_year=12.0, n_trials=5)
    assert a == b
    assert isinstance(a, ArmResult) and a.n_periods == 8


def test_sharpe_report_pass_gate_at_tstat_threshold():
    strong = np.array([0.03] * 30) + np.array([0.001 * ((-1) ** i) for i in range(30)])
    res = sharpe_report(strong, name="s", periods_per_year=12.0, n_trials=1, tstat_threshold=3.0)
    assert res.passes and res.tstat >= 3.0
    weak = np.array([0.01, -0.05, 0.06, -0.04, 0.02, -0.03, 0.05, -0.045])
    assert not sharpe_report(weak, name="w", periods_per_year=12.0, n_trials=50).passes
