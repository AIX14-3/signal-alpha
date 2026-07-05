"""수익률-시계열 백테스트 계층 — 특허 변동성 신호의 *경제가치*(샤프) 검정용.

기존 하니스(``evaluation``/``bakeoff``/``within_firm``)는 전부 **횡단면 IC·분류 지표**라
수익률/샤프/vol-타이밍 계층이 없다. 이 모듈이 그 빠진 계층을 채운다:

  수익률 구성(prices CSV) → vol-managed 오버레이(Moreira·Muir 2017) / 횡단면 inverse-vol
  → 지표(annualized Sharpe, t-stat, **Deflated Sharpe** = Bailey·López de Prado 2014).

배경(``ML/Patent/2026-07-01-handoff-magnitude.md`` §8·§9): 특허→실현변동성 횡단면 신호는
(a) embargo로 시점 누수 아님, (b) within-firm 분해로 **타이밍 아님·정적 특성**(between_ic≈±0.42,
within_ic≈0)으로 확정. ∴ 방향/타이밍 알파는 아니다. 남은 질문은 **"정적 vol 특성이 리스크
사이징 도구로서 경제가치가 있나"** 뿐 — 이 모듈이 그걸 정직하게(다중검정 DSR·t≥3) 측정한다.

순수 Python + numpy(+scipy.stats.norm). RNG·clock 없음 → 손수 만든 ``PriceSeries`` 로 결정론적
단위테스트 가능. 수익률은 **분수(fraction, 0.01 = +1%)** 로 통일.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import date

import numpy as np
from scipy.stats import norm

from .datalab_dataset import PriceSeries

# 오일러-마스케로니 상수 (expected-max-Sharpe 공식).
_EULER_GAMMA = 0.5772156649015329


# --------------------------------------------------------------------------- #
# 수익률 구성 (prices CSV에서 직접, Dataset 라벨과 독립 — 라벨 누수 방지)
# --------------------------------------------------------------------------- #
def daily_returns(prices: PriceSeries) -> dict[date, float]:
    """일별 단순수익률 ``close_t/close_{t-1}-1`` 을 후행일(t) 키로 반환(분수)."""
    out: dict[date, float] = {}
    closes, dates = prices.closes, prices.dates
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev == 0:
            continue
        out[dates[i]] = closes[i] / prev - 1.0
    return out


def equal_weight_basket(
    prices_by_ticker: dict[str, PriceSeries], tickers: list[str]
) -> dict[date, float]:
    """구성종목 일별수익률의 횡단면 평균(그날 수익률이 있는 종목만) → 일별 바스켓 시계열."""
    per_date: dict[date, list[float]] = {}
    for t in tickers:
        ps = prices_by_ticker.get(t)
        if ps is None:
            continue
        for d, r in daily_returns(ps).items():
            per_date.setdefault(d, []).append(r)
    return {d: sum(v) / len(v) for d, v in per_date.items() if v}


def period_returns(
    daily_ret: dict[date, float], rebalance_dates: list[date]
) -> tuple[list[date], np.ndarray]:
    """일별 시계열을 리밸런스 구간 ``(d_i, d_{i+1}]`` 내 복리로 묶어 기간수익률로.

    기간 i는 리밸런스 ``d_i`` 시점 정보(가중/예측)로 진입해 ``d_{i+1}`` 종가에 청산 → 기간 END
    날짜(d_{i+1})로 라벨. 그 구간에 거래일이 없으면 NaN(수익률 날조 금지).
    """
    items = sorted(daily_ret.items())
    dts = [d for d, _ in items]
    rs = [r for _, r in items]
    out_dates: list[date] = []
    out: list[float] = []
    for i in range(len(rebalance_dates) - 1):
        lo, hi = rebalance_dates[i], rebalance_dates[i + 1]
        a = bisect.bisect_right(dts, lo)
        b = bisect.bisect_right(dts, hi)
        out_dates.append(hi)
        if b <= a:
            out.append(float("nan"))
            continue
        prod = 1.0
        for r in rs[a:b]:
            prod *= 1.0 + r
        out.append(prod - 1.0)
    return out_dates, np.asarray(out, dtype=float)


def constituent_period_returns(
    prices_by_ticker: dict[str, PriceSeries],
    tickers: list[str],
    rebalance_dates: list[date],
) -> dict[str, np.ndarray]:
    """종목별 기간수익률 배열(길이 = len(rebalance_dates)-1). 거래일 없는 기간은 NaN."""
    out: dict[str, np.ndarray] = {}
    for t in tickers:
        ps = prices_by_ticker.get(t)
        if ps is None:
            continue
        _, arr = period_returns(daily_returns(ps), rebalance_dates)
        out[t] = arr
    return out


def equal_weight_period_returns(
    cpr: dict[str, np.ndarray], tickers: list[str]
) -> np.ndarray:
    """기간별 구성종목 평균(NaN 무시) → equal-weight 바스켓 기간수익률."""
    cols = [cpr[t] for t in tickers if t in cpr]
    if not cols:
        return np.asarray([], dtype=float)
    mat = np.vstack(cols)
    with np.errstate(invalid="ignore"):
        return np.nanmean(mat, axis=0)


def weighted_period_returns(
    cpr: dict[str, np.ndarray],
    tickers: list[str],
    weights_per_period: list[dict[str, float]],
) -> np.ndarray:
    """기간별 가중 바스켓. 리밸런스에서 정한 가중을 그 기간에 적용(결측 구성종목은 재정규화)."""
    cols = [cpr[t] for t in tickers if t in cpr]
    n = len(cols[0]) if cols else 0
    out = np.full(n, np.nan)
    for i in range(n):
        w = weights_per_period[i] if i < len(weights_per_period) else {}
        num = den = 0.0
        for t in tickers:
            arr = cpr.get(t)
            if arr is None or not np.isfinite(arr[i]) or t not in w:
                continue
            num += w[t] * arr[i]
            den += w[t]
        out[i] = num / den if den > 0 else np.nan
    return out


# --------------------------------------------------------------------------- #
# vol-managed 오버레이 (Moreira & Muir 2017) + 횡단면 inverse-vol
# --------------------------------------------------------------------------- #
def trailing_realized_variance(
    daily_ret: dict[date, float], as_of: date, lookback: int
) -> float | None:
    """``as_of`` 까지(포함) 마지막 ``lookback`` 개 일별수익률의 표본분산(ddof=1). t-1 기지 예측치.

    창이 2개 미만이면 ``None``(분산 정의 불가). 미래 정보 없음(≤ as_of만 사용).
    """
    window = sorted(d for d in daily_ret if d <= as_of)[-lookback:]
    if len(window) < 2:
        return None
    vals = [daily_ret[d] for d in window]
    m = sum(vals) / len(vals)
    return sum((v - m) ** 2 for v in vals) / (len(vals) - 1)


def vol_managed(base: np.ndarray, variance_forecast: np.ndarray) -> np.ndarray:
    """각 기간을 ``1/variance_forecast`` 로 스케일 후, 전체를 상수 c로 재스케일해 base와 무조건
    변동성 일치(Moreira-Muir c) → 샤프 직접 비교 가능. 예측 무효(NaN/≤0) 기간은 NaN.

    무타이밍(예측이 상수)이면 스케일이 균일 → base와 동일 시계열(샤프 불변)이 성질상 보장된다.
    """
    base = np.asarray(base, dtype=float)
    vf = np.asarray(variance_forecast, dtype=float)
    mask = np.isfinite(base) & np.isfinite(vf) & (vf > 0)
    managed = np.full(base.shape, np.nan)
    if not mask.any():
        return managed
    raw = base[mask] / vf[mask]
    b = base[mask]
    sb = b.std(ddof=1) if b.size >= 2 else 0.0
    sr = raw.std(ddof=1) if raw.size >= 2 else 0.0
    c = sb / sr if sr > 0 else 0.0
    managed[mask] = c * raw
    return managed


def inverse_vol_weights(vol_score_by_ticker: dict[str, float]) -> dict[str, float]:
    """가중 ∝ 1/vol_score, 합1 정규화 — 정적 vol 특성의 정직한 쓰임(저변동/리스크패리티).

    ``vol_score`` 는 **예측 변동성에 비례하는 양수**(높을수록 고변동). 비양수/NaN 제거, <2 생존이면
    제공된 전 종목 equal-weight fallback(빈약 횡단면이 특허 arm 행세 못 하게).
    """
    valid = {
        t: s
        for t, s in vol_score_by_ticker.items()
        if s is not None and np.isfinite(s) and s > 0
    }
    if len(valid) < 2:
        tickers = list(vol_score_by_ticker)
        w = 1.0 / len(tickers) if tickers else 0.0
        return {t: w for t in tickers}
    inv = {t: 1.0 / s for t, s in valid.items()}
    tot = sum(inv.values())
    return {t: v / tot for t, v in inv.items()}


# --------------------------------------------------------------------------- #
# 지표 — annualized Sharpe, t-stat, PSR/DSR (Bailey·López de Prado 2014)
# --------------------------------------------------------------------------- #
def _finite(returns) -> np.ndarray:
    a = np.asarray(returns, dtype=float)
    return a[np.isfinite(a)]


def annualized_sharpe(returns, periods_per_year: float) -> float:
    """연율화 샤프 = mean/std(ddof=1) × √periods_per_year."""
    r = _finite(returns)
    if r.size < 2:
        return float("nan")
    sd = r.std(ddof=1)
    if sd == 0:
        return float("nan")
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def sharpe_tstat(returns) -> float:
    """per-period 샤프 × √N = 평균수익률의 t-통계량."""
    r = _finite(returns)
    if r.size < 2:
        return float("nan")
    sd = r.std(ddof=1)
    if sd == 0:
        return float("nan")
    return float(r.mean() / sd * math.sqrt(r.size))


def skew_kurt(returns) -> tuple[float, float]:
    """모집단 왜도 γ3, **비초과(Pearson) 첨도** γ4(정규분포=3). σ는 ddof=0."""
    r = _finite(returns)
    if r.size < 2:
        return float("nan"), float("nan")
    m = r.mean()
    sd = r.std(ddof=0)
    if sd == 0:
        return 0.0, 3.0
    z = (r - m) / sd
    return float((z**3).mean()), float((z**4).mean())


def probabilistic_sharpe_ratio(returns, sr_benchmark_per_period: float = 0.0) -> float:
    """PSR = Φ((SR̂ − SR*)·√(n−1) / √(1 − γ3·SR̂ + (γ4−1)/4·SR̂²)). 전부 per-period 샤프."""
    r = _finite(returns)
    n = r.size
    if n < 3:
        return float("nan")
    sd = r.std(ddof=1)
    if sd == 0:
        return float("nan")
    sr = r.mean() / sd
    g3, g4 = skew_kurt(r)
    denom = 1.0 - g3 * sr + ((g4 - 1.0) / 4.0) * sr * sr
    if denom <= 0:
        return float("nan")
    z = (sr - sr_benchmark_per_period) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(norm.cdf(z))


def expected_max_sharpe(var_sr: float, n_trials: int) -> float:
    """SR0 = √V·[(1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e))] — N회 trial의 기대 최대 샤프(per-period)."""
    n = max(2, int(n_trials))
    if var_sr <= 0:
        return 0.0
    a = norm.ppf(1.0 - 1.0 / n)
    b = norm.ppf(1.0 - 1.0 / (n * math.e))
    return float(math.sqrt(var_sr) * ((1.0 - _EULER_GAMMA) * a + _EULER_GAMMA * b))


def deflated_sharpe_ratio(returns, n_trials: int, var_sr: float | None = None) -> float:
    """DSR = PSR(SR* = SR0(N trials)). var_sr 미지정 시 SR̂ 추정치의 해석적 표본분산 사용.

    N회 시도 중 우연히 최고를 뽑는 효과를 SR0로 깎아, 다중검정 후에도 샤프가 유의한지 확률로.
    """
    r = _finite(returns)
    n = r.size
    if n < 3:
        return float("nan")
    sd = r.std(ddof=1)
    if sd == 0:
        return float("nan")
    sr = r.mean() / sd
    g3, g4 = skew_kurt(r)
    if var_sr is None:
        denom = 1.0 - g3 * sr + ((g4 - 1.0) / 4.0) * sr * sr
        var_sr = max(denom, 0.0) / (n - 1)  # SR̂의 해석적 표본분산(per-period)
    sr0 = expected_max_sharpe(var_sr, n_trials)
    return probabilistic_sharpe_ratio(r, sr0)


@dataclass(frozen=True)
class ArmResult:
    """한 arm의 경제가치 요약. dsr=디플레이션 후 유의확률, passes=t≥threshold."""

    name: str
    n_periods: int
    ann_sharpe: float
    tstat: float
    dsr: float
    passes: bool


def sharpe_report(
    returns,
    *,
    name: str,
    periods_per_year: float,
    n_trials: int,
    var_sr: float | None = None,
    tstat_threshold: float = 3.0,
) -> ArmResult:
    """한 arm의 연율화 샤프·t-stat·DSR·(t≥threshold) 판정을 묶어 ArmResult로."""
    r = _finite(returns)
    t = sharpe_tstat(r)
    return ArmResult(
        name=name,
        n_periods=int(r.size),
        ann_sharpe=annualized_sharpe(r, periods_per_year),
        tstat=t,
        dsr=deflated_sharpe_ratio(r, n_trials, var_sr),
        passes=bool(np.isfinite(t) and t >= tstat_threshold),
    )


__all__ = [
    "ArmResult",
    "annualized_sharpe",
    "constituent_period_returns",
    "daily_returns",
    "deflated_sharpe_ratio",
    "equal_weight_basket",
    "equal_weight_period_returns",
    "expected_max_sharpe",
    "inverse_vol_weights",
    "period_returns",
    "probabilistic_sharpe_ratio",
    "sharpe_report",
    "sharpe_tstat",
    "skew_kurt",
    "trailing_realized_variance",
    "vol_managed",
    "weighted_period_returns",
]
