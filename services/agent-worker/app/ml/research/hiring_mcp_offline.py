"""MCP-오프라인 채용 로더 — DB 드라이버(asyncpg) 없이 덤프 행으로 매출 나우캐스트 Dataset 구성.

`hiring_db._fetch(precise=True)` 를 **접속 계층만 교체**해 재현한다. Supabase MCP(execute_sql)로
뽑아 로컬에 저장한
  (1) stocks 행     : (id, ticker, name, short_name)
  (2) HIRING 포스팅 : {source_name, observed_date, duty_groups}
을 받아, **기존 함수 그대로**(`unique_norm_map`·`_norm_name`·`_as_duty_list`) precise rematch 한 뒤
`fundamentals_dataset.build_revenue_dataset` 에 넘긴다. 매출 라벨은 `fundamentals_dart` 가 만든
`revenue_dart.csv`(`load_revenue_csv`)에서 읽는다. **로직 재구현 없음** → confirmed 런과 동형.

용도: 이 머신엔 DATABASE_URL 이 없어 `_fetch` 를 못 쓰지만, MCP 로는 같은 prod 를 읽을 수 있다.
포스팅 rematch 는 source_name 만 보므로, 유니버스 선정은 (2) 대신 distinct name+count(175행)로 족하다.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .fundamentals_dataset import build_revenue_dataset, load_revenue_csv
from .hiring_db import _as_duty_list, _norm_name, unique_norm_map

# 덤프 행 타입 별칭(가독성용).
StockRow = tuple[int, str, str | None, str | None]  # (id, ticker, name, short_name)


def _universe_norm_map(
    stocks_rows: Iterable[StockRow], tickers: set[str] | None
) -> tuple[dict[str, int], dict[str, int]]:
    """유니버스 stocks 로 {정규화이름→stock_id} 와 {ticker→stock_id}. tickers=None 이면 전 종목."""
    srows = [
        r for r in stocks_rows
        if tickers is None or r[1] in tickers
    ]
    unique_map = unique_norm_map((sid, name, short) for sid, _t, name, short in srows)
    id_by_ticker = {t: int(sid) for sid, t, _n, _s in srows}
    return unique_map, id_by_ticker


def posting_counts_by_ticker(
    stocks_rows: Iterable[StockRow],
    name_counts: Iterable[tuple[str, int]],
) -> dict[str, int]:
    """distinct source_name+count → {ticker: 총 포스팅수} (precise rematch 규약).

    유니버스 선정(clean = ≥N 포스팅)용. 전 종목(208)을 후보로 rematch — `_fetch` 와 동일한
    exact-normalized 매칭·모호이름 드롭을 그대로 쓴다.
    """
    stocks_rows = list(stocks_rows)
    unique_map, _ = _universe_norm_map(stocks_rows, tickers=None)
    ticker_by_id = {int(sid): t for sid, t, _n, _s in stocks_rows}
    counts: dict[str, int] = defaultdict(int)
    for source_name, n in name_counts:
        sid = unique_map.get(_norm_name(source_name or ""))
        if sid is not None:
            counts[ticker_by_id[sid]] += int(n)
    return dict(counts)


def rematch_postings(
    stocks_rows: Iterable[StockRow],
    postings: Iterable[dict],
    *,
    tickers: set[str],
    with_duty: bool,
) -> tuple[dict[str, int], dict[int, list[dict]], int, int]:
    """`_fetch(precise=True)` 재현 — (id_by_ticker, hiring_rows_by_stock, matched, dropped).

    postings 각 원소: {"source_name", "observed_date", "duty_groups"?}. observed_date 는
    문자열('YYYY-MM-DD') 그대로 넘긴다 — `build_revenue_dataset` 가 내부에서 `_as_date` 처리.
    """
    unique_map, id_by_ticker = _universe_norm_map(stocks_rows, tickers)
    by_stock: dict[int, list[dict]] = defaultdict(list)
    matched = dropped = 0
    for p in postings:
        sid = unique_map.get(_norm_name(p.get("source_name") or ""))
        if sid is None:
            dropped += 1
            continue
        row: dict[str, Any] = {"observed_date": p.get("observed_date")}
        if with_duty:
            row["duty_groups"] = _as_duty_list(p.get("duty_groups"))
        by_stock[sid].append(row)
        matched += 1
    return id_by_ticker, dict(by_stock), matched, dropped


def build_dataset_from_dumps(
    *,
    stocks_rows: Iterable[StockRow],
    postings: Iterable[dict],
    revenue_csv: str,
    tickers: list[str],
    feature_set: str = "volume+duty",
    lookback_days: int = 90,
    min_observations: int = 2,
    min_cross_section: int = 6,
    signal_step_days: int = 0,
    n_signal_steps: int = 3,
    label_mode: str = "yoy",
):
    """MCP 덤프 + 매출 CSV → 매출 나우캐스트 Dataset (`load_from_env` 오프라인 대체).

    ``signal_step_days>0`` 이면 분기 라벨을 월별 as_of 로 샘플링(횡단면↑). 누수 방지는
    스윕 embargo(revenue 라벨 horizon) 몫 — :func:`build_revenue_dataset` 참조.
    ``label_mode="surprise"`` 이면 라벨을 YoY 대신 매출 SUE(within-firm 놀라움)로.
    """
    with_duty = feature_set in ("duty", "volume+duty")
    id_by_ticker, hiring_rows_by_stock, matched, dropped = rematch_postings(
        stocks_rows, postings, tickers=set(tickers), with_duty=with_duty
    )
    print(f"[mcp-offline precise] rematched {matched} postings to "
          f"{len(hiring_rows_by_stock)} universe stocks; dropped {dropped}")
    rev_by_ticker = load_revenue_csv(revenue_csv)
    revenue_by_stock = {
        sid: rev_by_ticker[t] for t, sid in id_by_ticker.items() if t in rev_by_ticker
    }
    return build_revenue_dataset(
        hiring_rows_by_stock=hiring_rows_by_stock,
        revenue_by_stock=revenue_by_stock,
        lookback_days=lookback_days,
        feature_set=feature_set,
        min_observations=min_observations,
        min_cross_section=min_cross_section,
        signal_step_days=signal_step_days,
        n_signal_steps=n_signal_steps,
        label_mode=label_mode,
    )


__all__ = [
    "posting_counts_by_ticker",
    "rematch_postings",
    "build_dataset_from_dumps",
]
