"""Build a real (X, y) dataset for the bake-off from HIRING postings + prices.

Mirrors ``datalab_dataset`` but for hiring. Hiring is *event-like and strongly
seasonal* (Korean 정기공채: spring/fall peaks), so raw posting counts encode the
calendar, not company-specific demand. This module therefore engineers a small,
de-seasonalized, point-in-time feature set instead of feeding raw counts:

  hiring__deseason_momentum  recent-half vs prior-half de-seasonalized posting flow
  hiring__yoy_change         this window's flow vs the same window one year earlier
  hiring__days_since_latest  recency of the latest posting known at ``as_of``

Leakage guards (non-negotiable, same as DataLab):
1. Features at ``as_of`` use only postings with ``observed_date <= as_of`` (the
   posting's KST publish date is the knowledge time).
2. The label uses only prices STRICTLY AFTER ``as_of``.

The seasonal index is an empirical monthly factor estimated from the supplied
postings (pooled across stocks for stability given the tiny per-stock sample). It
normalizes a *feature* (calendar structure), not the label, so its in-sample
nature does not leak the return signal.

Pure Python + numpy: callers pass already-fetched rows (see ``hiring_db``), so the
transformation is deterministic and unit-testable without a DB.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import numpy as np

# Reuse the DataLab harness primitives verbatim (no parallel re-implementation).
from .datalab_dataset import Dataset, PriceSeries, _as_date, weekly_signal_dates
from .features import feature_matrix
from .labels import make_label
from .magnitude import (
    MAGNITUDE_TARGETS,
    abs_excess,
    cross_sectional_median_labels,
    forward_realized_vol,
)

_YEAR_DAYS = 365

# Explicit set of jasoseol duty-group names that are software / data / hardware
# R&D / engineering roles (the "what are they building" signal). Curated by exact
# name from the 145-name taxonomy so it is transparent and defensible; borderline
# sales/design roles (e.g. IT·솔루션·기술영업, UI·UX디자인) are deliberately
# excluded to keep "tech" = product-building.
TECH_DUTIES: frozenset[str] = frozenset({
    "게임개발", "SW·솔루션·ERP", "네트워크·보안·운영", "웹개발",
    "데이터엔지니어·분석·DBA", "서버·백엔드개발", "빅데이터·AI(인공지능)",
    "데이터분석", "HW·임베디드", "데이터엔지니어", "DBA", "시스템프로그래머",
    "응용프로그래머", "앱개발", "프론트엔드개발", "안드로이드개발", "iOS개발",
    "HTML·퍼블리싱", "QA", "통신기술·네트워크 구축", "전기·전자·제어",
    "기계설계·CAD·CAM", "반도체·디스플레이", "전기·통신",
})

VOLUME_FEATURES = (
    "hiring__deseason_momentum",
    "hiring__yoy_change",
    "hiring__days_since_latest",
)
DUTY_FEATURES = (
    "hiring__tech_share",
    "hiring__tech_share_yoy",
    "hiring__tech_share_mom",
)


def duty_tally(names: list[str] | None) -> tuple[int, int]:
    """(tech-duty count, total-duty count) for one posting's duty-group names."""
    if not names:
        return 0, 0
    uniq = list(dict.fromkeys(names))  # collector dedups already; be defensive
    tech = sum(1 for n in uniq if n in TECH_DUTIES)
    return tech, len(uniq)


def _pooled_tech_share(window: list[tuple[date, int, int]]) -> float:
    """Pooled tech share over postings in a window: sum(tech)/sum(total)."""
    tech = sum(t for _, t, _ in window)
    tot = sum(c for _, _, c in window)
    return tech / tot if tot > 0 else float("nan")


def duty_features(
    duty_postings: list[tuple[date, int, int]],
    *,
    as_of: date,
    lookback_days: int,
) -> dict[str, float]:
    """Point-in-time duty-mix features at ``as_of``.

    ``duty_postings`` is one stock's ``(date, n_tech, n_total)`` tallies ascending.
    All windows use only postings with ``date <= as_of`` (knowledge time), the same
    leakage guard as the volume features.

      hiring__tech_share      pooled tech share in the lookback window
      hiring__tech_share_yoy  window tech share minus the same window one year prior
      hiring__tech_share_mom  recent-half minus prior-half tech share (mix momentum)
    """
    lo = as_of - timedelta(days=lookback_days)
    window = [(d, t, c) for (d, t, c) in duty_postings if lo < d <= as_of and c > 0]
    share = _pooled_tech_share(window)

    y_lo = as_of - timedelta(days=_YEAR_DAYS + lookback_days)
    y_hi = as_of - timedelta(days=_YEAR_DAYS)
    prev = [(d, t, c) for (d, t, c) in duty_postings if y_lo < d <= y_hi and c > 0]
    prev_share = _pooled_tech_share(prev)
    yoy = (
        (share - prev_share)
        if (share == share and prev_share == prev_share)
        else float("nan")
    )

    mid = lo + (as_of - lo) / 2
    recent = [p for p in window if p[0] > mid]
    prior = [p for p in window if p[0] <= mid]
    rs, ps = _pooled_tech_share(recent), _pooled_tech_share(prior)
    mom = (rs - ps) if (rs == rs and ps == ps) else float("nan")

    return {
        "hiring__tech_share": share,
        "hiring__tech_share_yoy": yoy,
        "hiring__tech_share_mom": mom,
    }


def seasonal_index(observed_dates: list[date]) -> dict[int, float]:
    """Empirical month-of-year factor: ``month_share / uniform_share`` (mean ≈ 1).

    A factor > 1 means that calendar month is busier than average (e.g. 공채철),
    so de-seasonalizing divides posting weight by it. Months with no data or a
    non-positive factor fall back to 1.0 (no-op) so they never blow up a ratio.
    """
    counts = Counter(d.month for d in observed_dates)
    total = sum(counts.values())
    if total == 0:
        return {m: 1.0 for m in range(1, 13)}
    uniform = total / 12.0
    factors: dict[int, float] = {}
    for m in range(1, 13):
        f = counts.get(m, 0) / uniform if uniform > 0 else 1.0
        factors[m] = f if f > 0 else 1.0
    return factors


def _factor(factors: dict[int, float], month: int) -> float:
    f = factors.get(month, 1.0)
    return f if f > 0 else 1.0


def _wsum(dates: list[date], factors: dict[int, float]) -> float:
    """De-seasonalized posting weight: sum of 1/seasonal_factor over postings."""
    return sum(1.0 / _factor(factors, d.month) for d in dates)


def hiring_features(
    dates_sorted: list[date],
    *,
    as_of: date,
    lookback_days: int,
    factors: dict[int, float],
) -> tuple[dict[str, float], int]:
    """Point-in-time hiring features at ``as_of`` and the in-window posting count.

    ``dates_sorted`` is ALL of one stock's posting dates ascending; we slice
    point-in-time windows here (yoy needs dates older than the lookback window,
    so the full list is passed rather than a pre-sliced window).
    """
    lo = as_of - timedelta(days=lookback_days)
    window = [d for d in dates_sorted if lo < d <= as_of]
    n = len(window)

    # recent vs prior half (de-seasonalized) -> momentum
    mid = lo + (as_of - lo) / 2
    recent = [d for d in window if d > mid]
    prior = [d for d in window if d <= mid]
    rw, pw = _wsum(recent, factors), _wsum(prior, factors)
    momentum = (rw - pw) / pw if pw > 0 else float("nan")

    # this window vs same window one year earlier -> year-over-year change
    y_lo = as_of - timedelta(days=_YEAR_DAYS + lookback_days)
    y_hi = as_of - timedelta(days=_YEAR_DAYS)
    prev_window = [d for d in dates_sorted if y_lo < d <= y_hi]
    cur_w, prev_w = _wsum(window, factors), _wsum(prev_window, factors)
    yoy = (cur_w - prev_w) / prev_w if prev_w > 0 else float("nan")

    # recency of the latest posting known at as_of
    known = [d for d in dates_sorted if d <= as_of]
    days_since = float((as_of - max(known)).days) if known else float("nan")

    features = {
        "hiring__deseason_momentum": momentum,
        "hiring__yoy_change": yoy,
        "hiring__days_since_latest": days_since,
    }
    return features, n


def build_dataset(
    *,
    hiring_rows_by_stock: dict[int, list[dict]],
    prices_by_stock: dict[int, PriceSeries],
    signal_dates_by_stock: dict[int, list[date]],
    benchmark: PriceSeries | None = None,
    lookback_days: int = 90,
    horizon_sessions: int = 20,
    neutral_band_pct: float = 0.3,
    min_observations: int = 2,
    seasonal: dict[int, float] | None = None,
    feature_set: str = "volume",
    target: str = "direction",
    min_cross_section: int = 6,
) -> Dataset:
    """Assemble per-(stock, signal-date) hiring feature rows + forward-return labels.

    A sample is kept only when the lookback window has >= ``min_observations``
    postings AND a valid forward return AND a non-neutral direction; every drop is
    counted in ``Dataset.dropped`` so a tiny surviving sample can't pass silently.
    The seasonal index is estimated once from ALL supplied postings (pooled).

    ``feature_set`` selects which features to emit:
      * ``"volume"``       — the 3 de-seasonalized posting-flow features (default).
      * ``"duty"``         — the 3 duty-mix (tech-share) features only.
      * ``"volume+duty"``  — all 6.
    Duty features require each row to carry ``duty_groups`` (a list of duty names);
    rows without it tally as zero duties (NaN share -> imputed/dropped downstream).

    ``target`` selects what y means:
      * ``"direction"``    — up/down sign of forward excess return (default; neutral
        band drops near-zero moves). Unchanged legacy behaviour.
      * ``"abs_return"``   — |forward excess return| (size of the move vs benchmark).
      * ``"realized_vol"`` — forward realized volatility (std of daily returns over the
        horizon; absolute, benchmark-independent).
    For the two magnitude targets y is a **per-date cross-sectional** binary label
    (top half = big mover = 1, bottom half = 0; ``min_cross_section`` thin dates are
    dropped), and ``Dataset.excess_returns`` carries the continuous magnitude so
    ``rank_ic_xs`` scores how well a model ranks move size. No neutral band applies.
    """
    if feature_set not in ("volume", "duty", "volume+duty"):
        raise ValueError(f"unknown feature_set: {feature_set!r}")
    if target not in ("direction", *MAGNITUDE_TARGETS):
        raise ValueError(f"unknown target: {target!r}")
    want_volume = feature_set in ("volume", "volume+duty")
    want_duty = feature_set in ("duty", "volume+duty")
    # 매그니튜드 모드: 행별 매그니튜드를 모은 뒤(조립 후) per-date 횡단면으로 이진 라벨링.
    is_magnitude = target in MAGNITUDE_TARGETS

    # Parse + sort each stock's posting dates once; pool all for the seasonal index.
    # When duty features are requested, also build per-stock (date, n_tech, n_total)
    # tallies (point-in-time windows are sliced later, in duty_features).
    dates_by_stock: dict[int, list[date]] = {}
    duty_by_stock: dict[int, list[tuple[date, int, int]]] = {}
    pooled: list[date] = []
    for stock_id, rows in hiring_rows_by_stock.items():
        ds: list[date] = []
        tallies: list[tuple[date, int, int]] = []
        for r in rows:
            d = _as_date(r.get("observed_date"))
            if d is None:
                continue
            ds.append(d)
            if want_duty:
                tech, tot = duty_tally(r.get("duty_groups"))
                tallies.append((d, tech, tot))
        ds.sort()
        dates_by_stock[stock_id] = ds
        duty_by_stock[stock_id] = sorted(tallies, key=lambda t: t[0])
        pooled.extend(ds)
    factors = seasonal if seasonal is not None else seasonal_index(pooled)

    feat_rows: list[dict[str, float]] = []
    y: list[int] = []
    excess: list[float] = []
    mags: list[float] = []  # 매그니튜드 모드에서만 채워짐(조립 후 횡단면 라벨로 변환)
    dates: list[int] = []
    stock_ids: list[int] = []
    dropped: Counter = Counter()

    for stock_id, signal_dates in signal_dates_by_stock.items():
        dates_sorted = dates_by_stock.get(stock_id, [])
        duty_sorted = duty_by_stock.get(stock_id, [])
        prices = prices_by_stock.get(stock_id)
        if prices is None:
            dropped["no_price_series"] += len(signal_dates)
            continue
        for as_of in signal_dates:
            vol_features, n = hiring_features(
                dates_sorted, as_of=as_of, lookback_days=lookback_days, factors=factors
            )
            if n < min_observations:
                dropped["too_few_observations"] += 1
                continue
            features: dict[str, float] = {}
            if want_volume:
                features.update(vol_features)
            if want_duty:
                features.update(
                    duty_features(
                        duty_sorted, as_of=as_of, lookback_days=lookback_days
                    )
                )
            stock_ret = prices.forward_return_pct(as_of, horizon_sessions)
            if stock_ret is None:
                dropped["no_forward_price"] += 1
                continue
            bench_ret = (
                benchmark.forward_return_pct(as_of, horizon_sessions)
                if benchmark is not None
                else 0.0
            )
            if bench_ret is None:
                bench_ret = 0.0  # benchmark gap -> fall back to raw return

            if is_magnitude:
                # 방향 대신 "움직임의 크기"를 모은다. 라벨(큰/작은)은 조립 후 per-date
                # 횡단면으로 부여하므로 여기선 연속 매그니튜드만 적재(neutral band 무관).
                if target == "abs_return":
                    mag = abs_excess(stock_ret, bench_ret)
                else:  # realized_vol
                    rv = forward_realized_vol(prices, as_of, horizon_sessions)
                    if rv is None:
                        dropped["no_realized_vol"] += 1
                        continue
                    mag = rv
                feat_rows.append(features)
                mags.append(mag)
                dates.append(as_of.toordinal())
                stock_ids.append(stock_id)
                continue

            label = make_label(
                stock_return_pct=stock_ret,
                benchmark_return_pct=bench_ret,
                neutral_band_pct=neutral_band_pct,
            )
            if label.y_direction is None:
                dropped["neutral_band"] += 1
                continue
            feat_rows.append(features)
            y.append(label.y_direction)
            excess.append(label.excess_return_pct)
            dates.append(as_of.toordinal())
            stock_ids.append(stock_id)

    if is_magnitude:
        # per-date 횡단면 이진 라벨(상위 절반=1·하위 절반=0). 빈약한 날짜·중앙 행은 버려진다.
        keep, y = cross_sectional_median_labels(
            mags, dates, min_cross_section=min_cross_section
        )
        dropped["thin_cross_section"] += len(feat_rows) - len(keep)
        feat_rows = [feat_rows[i] for i in keep]
        excess = [mags[i] for i in keep]  # 연속 매그니튜드 → rank_ic_xs 채점용
        dates = [dates[i] for i in keep]
        stock_ids = [stock_ids[i] for i in keep]

    matrix, names = feature_matrix(feat_rows)
    return Dataset(
        X=np.array(matrix, dtype=float).reshape(len(feat_rows), len(names)),
        y=np.array(y, dtype=int),
        excess_returns=np.array(excess, dtype=float),
        dates=np.array(dates, dtype=int),
        stock_ids=np.array(stock_ids, dtype=int),
        feature_names=names,
        dropped=dropped,
    )


__all__ = [
    "build_dataset",
    "hiring_features",
    "duty_features",
    "duty_tally",
    "seasonal_index",
    "weekly_signal_dates",
    "TECH_DUTIES",
    "VOLUME_FEATURES",
    "DUTY_FEATURES",
]
