"""매출 나우캐스팅 데이터셋 — 채용 피처 → 차기 분기 매출 성장(YoY).

"채용→사업확장→매출"의 직접 인과 사슬을 검정한다(주가 방향 아님). 각 (종목, 분기)에서
**분기말**을 as_of로 잡아 그 시점까지의 채용 피처로 *그 분기의 YoY 매출 성장*을 맞추는지
본다. 라벨(매출)은 분기말 ~45일 뒤 공시(known_at)되므로 누수가 없다.

매그니튜드와 동일하게 y는 per-분기 횡단면 이진(상위 절반=고성장=1·하위=0), 연속 성장률은
``excess_returns`` 슬롯에 실어 ``rank_ic_xs`` 가 채점 → 16모델·permutation·BH-FDR 무변경 재사용.

매출 데이터는 연구 CSV(`fundamentals_dart.build_revenue_csv` 산출)에서 읽고, 채용 공고는
``hiring_db`` 의 prod read-only 인출을 재사용한다. ``labels.py``(B 소유)·팀 DART 수집기는
건드리지 않는다.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

import numpy as np

from .datalab_dataset import Dataset
from .features import feature_matrix
from .hiring_dataset import (
    duty_features,
    duty_tally,
    hiring_features,
    seasonal_index,
)
from .magnitude import cross_sectional_median_labels


def quarter_end_date(year: int, quarter: int) -> date:
    """분기말 캘린더 날짜(채용 피처 as_of)."""
    return {
        1: date(year, 3, 31),
        2: date(year, 6, 30),
        3: date(year, 9, 30),
        4: date(year, 12, 31),
    }[quarter]


def yoy_growth(
    rev_by_q: dict[tuple[int, int], float]
) -> dict[tuple[int, int], float]:
    """{(year,quarter): 단일분기매출} → {(year,quarter): 전년동기대비 성장률}.

    계절성 제거를 위해 QoQ가 아니라 **전년 동분기**(year-1, 같은 quarter) 대비. 전년 동분기가
    없거나 0이면 제외.
    """
    out: dict[tuple[int, int], float] = {}
    for (y, q), cur in rev_by_q.items():
        prior = rev_by_q.get((y - 1, q))
        if prior is not None and prior != 0:
            out[(y, q)] = cur / prior - 1.0
    return out


def load_revenue_csv(
    path: str,
) -> dict[str, dict[tuple[int, int], tuple[float, str]]]:
    """revenue_dart.csv → {ticker: {(year,quarter): (단일매출, known_at)}}."""
    out: dict[str, dict[tuple[int, int], tuple[float, str]]] = defaultdict(dict)
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["ticker"]][(int(r["year"]), int(r["quarter"]))] = (
                float(r["single_revenue_krw"]),
                r["known_at"],
            )
    return out


def build_revenue_dataset(
    *,
    hiring_rows_by_stock: dict[int, list[dict]],
    revenue_by_stock: dict[int, dict[tuple[int, int], tuple[float, str]]],
    lookback_days: int = 90,
    feature_set: str = "volume",
    min_observations: int = 2,
    min_cross_section: int = 6,
    signal_step_days: int = 0,
    n_signal_steps: int = 3,
) -> Dataset:
    """채용 피처(분기말 PIT) → 분기 YoY 매출성장 횡단면 라벨 데이터셋.

    ``revenue_by_stock``: stock_id → {(year,quarter): (단일매출, known_at)}.
    누수 차단: 피처는 as_of 이하 공고만, 라벨 성장률은 known_at(공시일) > as_of 일 때만.

    ``signal_step_days=0`` (기본): 분기당 as_of 1개(분기말) — 원래 quarterly 동작.
    ``signal_step_days>0``: 같은 분기 라벨을 as_of ∈ {분기말, 분기말−step, …}(총 ``n_signal_steps``개,
    모두 분기 내부·PIT ``known_at>as_of``)로 **월별 샘플링**해 횡단면을 늘린다(28분기→~84). 같은 분기
    라벨이 여러 as_of 에 공유되므로 **outcome-겹침 누수 방지 = 스윕 embargo 를 분기폭(~95일) 이상**으로
    둬야 한다(:mod:`search_grid` 의 revenue 라벨 horizon 이 그 embargo 를 만든다).
    """
    if feature_set not in ("volume", "duty", "volume+duty"):
        raise ValueError(f"unknown feature_set: {feature_set!r}")
    want_volume = feature_set in ("volume", "volume+duty")
    want_duty = feature_set in ("duty", "volume+duty")

    # 종목별 공고일/직무 tally 파싱 + 계절지수 풀링(hiring_dataset과 동일 규약).
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
    factors = seasonal_index(pooled)

    feat_rows: list[dict[str, float]] = []
    growths: list[float] = []
    qdates: list[int] = []
    stock_ids: list[int] = []
    dropped: Counter = Counter()

    for stock_id, rev_q in revenue_by_stock.items():
        rev_only = {k: v[0] for k, v in rev_q.items()}
        growth_by_q = yoy_growth(rev_only)
        dates_sorted = dates_by_stock.get(stock_id, [])
        duty_sorted = duty_by_stock.get(stock_id, [])
        for (year, quarter), growth in growth_by_q.items():
            q_end = quarter_end_date(year, quarter)
            known_at = _as_date(rev_q[(year, quarter)][1])
            if known_at is None or known_at <= q_end:
                dropped["leak_or_missing_known_at"] += 1  # 라벨이 분기말에 이미 알려짐→제외
                continue
            # as_of 스냅샷: quarterly(분기말 1개) 또는 monthly(분기말, −step, −2·step, …).
            if signal_step_days and signal_step_days > 0:
                as_ofs = [q_end - timedelta(days=signal_step_days * k)
                          for k in range(max(1, n_signal_steps))]
            else:
                as_ofs = [q_end]
            for as_of in as_ofs:
                if known_at <= as_of:  # PIT(분기말 스냅샷은 항상 통과; 방어적)
                    dropped["leak_or_missing_known_at"] += 1
                    continue
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
                        duty_features(duty_sorted, as_of=as_of, lookback_days=lookback_days)
                    )
                feat_rows.append(features)
                growths.append(growth)
                qdates.append(as_of.toordinal())
                stock_ids.append(stock_id)

    keep, y = cross_sectional_median_labels(
        growths, qdates, min_cross_section=min_cross_section
    )
    dropped["thin_cross_section"] += len(feat_rows) - len(keep)
    feat_rows = [feat_rows[i] for i in keep]
    excess = [growths[i] for i in keep]
    qdates = [qdates[i] for i in keep]
    stock_ids = [stock_ids[i] for i in keep]

    matrix, names = feature_matrix(feat_rows)
    return Dataset(
        X=np.array(matrix, dtype=float).reshape(len(feat_rows), len(names)),
        y=np.array(y, dtype=int),
        excess_returns=np.array(excess, dtype=float),
        dates=np.array(qdates, dtype=int),
        stock_ids=np.array(stock_ids, dtype=int),
        feature_names=names,
        dropped=dropped,
    )


def sector_neutralize_label(
    ds: Dataset,
    sector_by_stock: dict[int, str | None],
    *,
    min_cross_section: int = 6,
) -> Dataset:
    """라벨(매출성장)을 **(섹터×시점) 안에서 demean** 해 섹터 공통파도를 제거한다.

    "AI 붐에 테크 섹터가 다 같이 채용·매출↑" 같은 **섹터-시점 공통요인 교란**을 라벨에서 걷어낸다.
    각 (섹터, date) 그룹에서 성장률 평균을 빼고, 그 잔차로 **date 내부 재-이진화**한다. 섹터가
    한 종목뿐인 (섹터,date)나 섹터 미상 행은 중립화 불가 → 드롭. 이후 within-firm 게이트가
    "섹터를 걷어낸 뒤에도 회사가 자기 평소보다 더 뽑은 분기에 (섹터대비) 매출이 더 컸나"를 검정.
    """
    sec = [sector_by_stock.get(int(s)) for s in ds.stock_ids]
    growth = ds.excess_returns.astype(float).copy()
    dropped: Counter = Counter(ds.dropped)
    dates = ds.dates
    for d in np.unique(dates):
        on_date = np.where(dates == d)[0]
        groups: dict = defaultdict(list)
        for i in on_date:
            groups[sec[i]].append(i)
        for scv, idxs in groups.items():
            arr = np.array(idxs)
            if scv is None:
                growth[arr] = np.nan
                dropped["sector_neutral_no_sector"] += len(arr)
            elif len(arr) >= 2:
                growth[arr] = growth[arr] - np.nanmean(growth[arr])
            else:
                growth[arr] = np.nan
                dropped["sector_neutral_singleton"] += 1
    ok = np.where(np.isfinite(growth))[0]
    keep2, y = cross_sectional_median_labels(
        [float(growth[i]) for i in ok], [int(dates[i]) for i in ok],
        min_cross_section=min_cross_section,
    )
    final = ok[np.array(keep2, dtype=int)] if len(keep2) else np.array([], dtype=int)
    return Dataset(
        X=ds.X[final],
        y=np.array(y, dtype=int),
        excess_returns=growth[final],
        dates=dates[final],
        stock_ids=ds.stock_ids[final],
        feature_names=list(ds.feature_names),
        dropped=dropped,
    )


async def load_from_env(
    *,
    database_url: str,
    revenue_csv: str,
    tickers: list[str],
    lookback_days: int = 90,
    feature_set: str = "volume",
    min_observations: int = 2,
    min_cross_section: int = 6,
    precise_rematch: bool = True,
) -> Dataset:
    """prod 채용 공고(read-only) + 연구 매출 CSV → 매출 나우캐스팅 Dataset."""
    from .hiring_db import _fetch

    id_by_ticker, hiring_rows_by_stock = await _fetch(
        database_url, tickers, precise=precise_rematch,
        with_duty=feature_set in ("duty", "volume+duty"),
    )
    rev_by_ticker = load_revenue_csv(revenue_csv)
    revenue_by_stock: dict[int, dict[tuple[int, int], tuple[float, str]]] = {}
    for ticker, sid in id_by_ticker.items():
        if ticker in rev_by_ticker:
            revenue_by_stock[sid] = rev_by_ticker[ticker]

    return build_revenue_dataset(
        hiring_rows_by_stock=hiring_rows_by_stock,
        revenue_by_stock=revenue_by_stock,
        lookback_days=lookback_days,
        feature_set=feature_set,
        min_observations=min_observations,
        min_cross_section=min_cross_section,
    )


def build_patent_revenue_dataset(
    *,
    patent_rows_by_stock: dict[int, list[dict]],
    revenue_by_stock: dict[int, dict[tuple[int, int], tuple[float, str]]],
    lookback_days: int = 365,
    min_observations: int = 1,
    min_cross_section: int = 6,
    xs_normalize: str = "none",
    exclude_features: frozenset[str] = frozenset(),
) -> Dataset:
    """특허 피처(분기말 PIT, **publication 윈도**) → 분기 YoY 매출성장 횡단면 라벨.

    ``build_revenue_dataset``(채용)의 특허 버전 — 피처 추출만 특허 인디케이터
    (``compute_indicators``, publication-date 윈도 누수가드)로 교체하고, 나머지(분기말
    as_of, ``yoy_growth``, ``known_at`` 누수가드, 횡단면 중앙값 이진화)는 동일하다.
    특허는 희소하므로 lookback 기본 365일·min_observations 기본 1.
    """
    from dataclasses import asdict

    from app.analyzers.patent.indicators import compute_indicators

    from .features import build_feature_row
    from .patent_dataset import _cross_sectional_normalize, _window_rows

    feat_rows: list[dict[str, float]] = []
    growths: list[float] = []
    qdates: list[int] = []
    stock_ids: list[int] = []
    dropped: Counter = Counter()

    for stock_id, rev_q in revenue_by_stock.items():
        rev_only = {k: v[0] for k, v in rev_q.items()}
        growth_by_q = yoy_growth(rev_only)
        rows = patent_rows_by_stock.get(stock_id, [])
        for (year, quarter), growth in growth_by_q.items():
            as_of = quarter_end_date(year, quarter)
            known_at = _as_date(rev_q[(year, quarter)][1])
            if known_at is None or known_at <= as_of:
                dropped["leak_or_missing_known_at"] += 1  # 라벨이 as_of에 이미 공시됨→제외
                continue
            window = _window_rows(rows, as_of=as_of, lookback_days=lookback_days)
            indicators = compute_indicators(window, as_of=as_of, lookback_days=lookback_days)
            if indicators.total < min_observations:
                dropped["too_few_filings"] += 1
                continue
            ind = asdict(indicators)
            ind.pop("latest_application_date", None)
            feat_rows.append(build_feature_row("patent", ind))
            growths.append(growth)
            qdates.append(as_of.toordinal())
            stock_ids.append(stock_id)

    keep, y = cross_sectional_median_labels(
        growths, qdates, min_cross_section=min_cross_section
    )
    dropped["thin_cross_section"] += len(feat_rows) - len(keep)
    feat_rows = [feat_rows[i] for i in keep]
    excess = [growths[i] for i in keep]
    qdates = [qdates[i] for i in keep]
    stock_ids = [stock_ids[i] for i in keep]

    matrix, names = feature_matrix(feat_rows)
    X = (
        np.array(matrix, dtype=float).reshape(len(feat_rows), len(names))
        if feat_rows
        else np.empty((0, 0))
    )
    dates_arr = np.array(qdates, dtype=int)
    if exclude_features and names:
        kept = [i for i, n in enumerate(names) if n not in exclude_features]
        names = [names[i] for i in kept]
        X = X[:, kept] if X.size else X
    X = _cross_sectional_normalize(X, dates_arr, names, xs_normalize)
    return Dataset(
        X=X,
        y=np.array(y, dtype=int),
        excess_returns=np.array(excess, dtype=float),
        dates=dates_arr,
        stock_ids=np.array(stock_ids, dtype=int),
        feature_names=names,
        dropped=dropped,
    )


async def load_patent_revenue_from_env(
    *,
    database_url: str,
    revenue_csv: str,
    tickers: list[str],
    lookback_days: int = 365,
    min_observations: int = 1,
    min_cross_section: int = 6,
    xs_normalize: str = "none",
) -> Dataset:
    """prod 특허(read-only) + 연구 매출 CSV → 특허→차기분기 매출 나우캐스팅 Dataset."""
    from signal_alpha_data_access import DatabaseSettings, create_pool

    from .datalab_db import resolve_stock_ids
    from .patent_db import fetch_patent_rows

    rev_by_ticker = load_revenue_csv(revenue_csv)
    pool = await create_pool(DatabaseSettings(database_url=database_url))
    try:
        async with pool.acquire() as conn:
            id_by_ticker = await resolve_stock_ids(conn, tickers)
        missing = [t for t in tickers if t not in id_by_ticker]
        if missing:
            raise ValueError(f"tickers not found in stocks table: {missing}")
        stock_ids = [id_by_ticker[t] for t in tickers]
        patent_rows_by_stock = await fetch_patent_rows(pool, stock_ids)
    finally:
        await pool.close()

    revenue_by_stock: dict[int, dict[tuple[int, int], tuple[float, str]]] = {}
    for ticker, sid in id_by_ticker.items():
        if ticker in rev_by_ticker:
            revenue_by_stock[sid] = rev_by_ticker[ticker]

    return build_patent_revenue_dataset(
        patent_rows_by_stock=patent_rows_by_stock,
        revenue_by_stock=revenue_by_stock,
        lookback_days=lookback_days,
        min_observations=min_observations,
        min_cross_section=min_cross_section,
        xs_normalize=xs_normalize,
    )


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


__all__ = [
    "quarter_end_date",
    "yoy_growth",
    "load_revenue_csv",
    "build_revenue_dataset",
    "load_from_env",
    "build_patent_revenue_dataset",
    "load_patent_revenue_from_env",
]
