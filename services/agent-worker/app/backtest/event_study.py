"""event_study — L6 forward-return 라벨 + lift 채택 게이트.

핵심 규율(D3 look-ahead 0):
- **진입(entry) = event_date 다음 영업일** = 가격 시계열에서 ``event_date`` 보다 *엄격히 큰*
  첫 세션. 이벤트 당일 종가는 base 로도 쓰지 않는다(당일 종가 금지). asof_date = 진입 세션일.
- forward_return_k = close[entry+k] / close[entry] − 1 (진입 후 k세션 누적수익). 윈도가 아직
  경과하지 않았으면(미래 세션 부족) None — 미실현 미래가 라벨을 오염시키지 않는다.
- abnormal_return_20d = 종목 20d forward − kospi20 벤치 20d forward(시장초과). 둘 중 하나라도
  None 이면 None.

가격 시계열은 ``{stock_id: PriceSeries}`` 형태(오름차순 dates/closes). 벤치마크도 동일.
순수 계산 함수(``resolve_entry_index``/``forward_returns_from_entry``/``compute_abnormal_return``/
``compute_lift``)는 DB 없이 단위테스트 가능하며, ``EventStudyBuilder`` 가 이를 패널 행으로 조립한다.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

DEFAULT_HORIZONS: tuple[int, ...] = (1, 5, 20)
ABNORMAL_HORIZON = 20
DEFAULT_UNIVERSE = "kospi20_seed"

# lift 채택 게이트
MIN_N_PER_GROUP = 30
BOOTSTRAP_ITERS = 2000


@dataclass(frozen=True)
class PriceSeries:
    """오름차순 거래일/종가. dates[i] 의 종가가 closes[i]."""

    dates: Sequence[date]
    closes: Sequence[float]


@dataclass(frozen=True)
class PanelRow:
    signal_event_id: int
    stock_id: int
    asof_date: date
    fwd_return_1d: float | None
    fwd_return_5d: float | None
    fwd_return_20d: float | None
    abnormal_return_20d: float | None
    universe_snapshot: str = DEFAULT_UNIVERSE


# --------------------------------------------------------------------------- #
# 순수 계산
# --------------------------------------------------------------------------- #
def resolve_entry_index(dates: Sequence[date], event_date: date) -> int | None:
    """event_date 다음 영업일(엄격히 큰 첫 세션)의 인덱스. 이후 세션이 없으면 None.

    ``bisect_right`` 라 event_date 당일 세션은 진입에서 제외된다(당일 종가 금지).
    """
    i = bisect_right(list(dates), event_date)
    if i >= len(dates):
        return None
    return i


def forward_returns_from_entry(
    closes: Sequence[float], entry_idx: int, horizons: Iterable[int] = DEFAULT_HORIZONS
) -> dict[int, float | None]:
    """진입 인덱스에서 각 horizon 의 누적수익. 미래 세션 부족/0가 base 면 None."""
    out: dict[int, float | None] = {}
    if entry_idx < 0 or entry_idx >= len(closes):
        return {k: None for k in horizons}
    base = float(closes[entry_idx])
    for k in horizons:
        j = entry_idx + k
        if base and j < len(closes):
            out[k] = float(closes[j]) / base - 1.0
        else:
            out[k] = None
    return out


def compute_forward_returns(
    series: PriceSeries, event_date: date, horizons: Iterable[int] = DEFAULT_HORIZONS
) -> tuple[date | None, dict[int, float | None]]:
    """(asof_date, {horizon: forward_return}). 진입 세션이 없으면 (None, 전부 None)."""
    horizons = tuple(horizons)
    entry = resolve_entry_index(series.dates, event_date)
    if entry is None:
        return None, {k: None for k in horizons}
    asof_date = series.dates[entry]
    return asof_date, forward_returns_from_entry(series.closes, entry, horizons)


def compute_abnormal_return(
    stock_return: float | None, benchmark_return: float | None
) -> float | None:
    """시장초과수익 = 종목 − 벤치. 한쪽이라도 None 이면 None."""
    if stock_return is None or benchmark_return is None:
        return None
    return float(stock_return) - float(benchmark_return)


# --------------------------------------------------------------------------- #
# lift 채택 게이트
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GroupStat:
    n: int
    mean: float | None
    hit_rate: float | None


def _mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _hit_rate(xs: Sequence[float]) -> float | None:
    return (sum(1 for x in xs if x > 0) / len(xs)) if xs else None


def _permutation_pvalue(
    samples: Sequence[tuple[str, float]], treatment: str, iters: int
) -> float | None:
    """양측 순열검정: treatment 그룹 평균수익 vs 전체 baseline.

    결정론 셔플(RNG 없이 stride 회전) — 시드 없이 재현 가능(``backtest_cause_lift`` 규율).
    """
    labels = [g for g, _ in samples]
    rets = [r for _, r in samples]
    treat = [r for g, r in samples if g == treatment]
    if not treat or len(set(labels)) < 2:
        return None
    baseline_mean = _mean(rets)
    if baseline_mean is None:
        return None
    observed = abs((_mean(treat) or 0.0) - baseline_mean)
    n = len(rets)
    extreme = 0
    for i in range(1, iters + 1):
        stride = 1 + (i % (n - 1)) if n > 1 else 1
        rotated = labels[stride:] + labels[:stride]
        shuffled = [rets[j] for j in range(n) if rotated[j] == treatment]
        if shuffled and abs((_mean(shuffled) or 0.0) - baseline_mean) >= observed:
            extreme += 1
    return extreme / iters


def compute_lift(
    samples: Iterable[tuple[str, float | None]],
    *,
    treatment: str,
    min_n: int = MIN_N_PER_GROUP,
    iters: int = BOOTSTRAP_ITERS,
) -> dict[str, Any]:
    """그룹별 forward-return 통계 + treatment lift(vs baseline) + 순열검정 p.

    ``samples`` 는 (group_label, forward_return) — None/비유한 수익은 제외. 표본이 게이트
    (min_n) 미만이면 ``gated=True`` 로 lift 판정을 보류한다(과소표본 lift 주장 차단).
    """
    clean: list[tuple[str, float]] = [
        (str(g), float(v))
        for g, v in samples
        if v is not None and math.isfinite(float(v))
    ]
    groups: dict[str, list[float]] = {}
    for g, v in clean:
        groups.setdefault(g, []).append(v)
    stats = {g: GroupStat(len(xs), _mean(xs), _hit_rate(xs)) for g, xs in groups.items()}
    baseline = [v for _, v in clean]
    treat = groups.get(treatment, [])

    result: dict[str, Any] = {
        "groups": stats,
        "baseline_n": len(baseline),
        "baseline_mean": _mean(baseline),
        "treatment": treatment,
    }
    if len(treat) >= min_n and len(baseline) >= min_n:
        result["lift"] = (_mean(treat) or 0.0) - (_mean(baseline) or 0.0)
        result["pvalue"] = _permutation_pvalue(clean, treatment, iters)
        result["gated"] = False
    else:
        result["lift"] = None
        result["pvalue"] = None
        result["gated"] = True
    return result


# --------------------------------------------------------------------------- #
# 패널 조립
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SignalEventRef:
    signal_event_id: int
    stock_id: int
    event_date: date


class EventStudyBuilder:
    """signal_events → event_study_panel 패널 행 조립.

    순수 ``build_rows`` 는 주입된 가격 시계열로 행을 만든다(DB 불필요, 단위테스트 대상).
    ``run`` 은 connection 으로 이벤트/가격을 적재하고 ``EventStudyRepository`` 로 upsert 한다.
    """

    def __init__(
        self,
        *,
        horizons: Iterable[int] = DEFAULT_HORIZONS,
        universe_snapshot: str = DEFAULT_UNIVERSE,
    ) -> None:
        self._horizons = tuple(horizons)
        self._universe = universe_snapshot

    def build_rows(
        self,
        events: Iterable[SignalEventRef],
        price_series: Mapping[int, PriceSeries],
        benchmark: PriceSeries | None,
    ) -> list[PanelRow]:
        rows: list[PanelRow] = []
        for ev in events:
            series = price_series.get(ev.stock_id)
            if series is None:
                continue  # 가격 없음 → 라벨 불가
            asof_date, fwd = compute_forward_returns(series, ev.event_date, self._horizons)
            if asof_date is None:
                continue  # event_date 이후 세션 없음(미래 윈도 미경과)

            abnormal: float | None = None
            if benchmark is not None and ABNORMAL_HORIZON in fwd:
                _, bench_fwd = compute_forward_returns(
                    benchmark, ev.event_date, (ABNORMAL_HORIZON,)
                )
                abnormal = compute_abnormal_return(
                    fwd.get(ABNORMAL_HORIZON), bench_fwd.get(ABNORMAL_HORIZON)
                )

            rows.append(
                PanelRow(
                    signal_event_id=ev.signal_event_id,
                    stock_id=ev.stock_id,
                    asof_date=asof_date,
                    fwd_return_1d=fwd.get(1),
                    fwd_return_5d=fwd.get(5),
                    fwd_return_20d=fwd.get(20),
                    abnormal_return_20d=abnormal,
                    universe_snapshot=self._universe,
                )
            )
        return rows

    async def run(
        self,
        connection: Any,
        *,
        event_filter_sql: str = "",
        event_filter_args: Sequence[Any] = (),
    ) -> int:
        """이벤트/가격을 적재 → 패널 행 조립 → event_study_panel upsert. 적재 행수 반환."""
        from signal_alpha_data_access.repositories import EventStudyRepository

        repo = EventStudyRepository(connection)
        events = await _load_signal_events(connection, event_filter_sql, event_filter_args)
        price_series = await _load_price_series(connection)
        benchmark = await _load_benchmark_series(connection)

        rows = self.build_rows(events, price_series, benchmark)
        for row in rows:
            await repo.upsert_panel_row(
                signal_event_id=row.signal_event_id,
                stock_id=row.stock_id,
                asof_date=row.asof_date,
                fwd_return_1d=row.fwd_return_1d,
                fwd_return_5d=row.fwd_return_5d,
                fwd_return_20d=row.fwd_return_20d,
                abnormal_return_20d=row.abnormal_return_20d,
                universe_snapshot=row.universe_snapshot,
            )
        return len(rows)


# --------------------------------------------------------------------------- #
# DB 로딩 (run 전용 — 단위테스트는 build_rows 를 직접 호출)
# --------------------------------------------------------------------------- #
async def _load_signal_events(
    connection: Any, filter_sql: str, filter_args: Sequence[Any]
) -> list[SignalEventRef]:
    rows = await connection.fetch(
        "SELECT id, stock_id, event_date FROM signal_events"
        + (f" WHERE {filter_sql}" if filter_sql else "")
        + " ORDER BY event_date ASC, id ASC",
        *filter_args,
    )
    return [
        SignalEventRef(int(r["id"]), int(r["stock_id"]), r["event_date"]) for r in rows
    ]


async def _load_price_series(connection: Any) -> dict[int, PriceSeries]:
    rows = await connection.fetch(
        "SELECT stock_id, trade_date, close FROM ohlcv_data ORDER BY stock_id, trade_date"
    )
    acc: dict[int, tuple[list[date], list[float]]] = {}
    for r in rows:
        dates, closes = acc.setdefault(int(r["stock_id"]), ([], []))
        dates.append(r["trade_date"])
        closes.append(float(r["close"]))
    return {sid: PriceSeries(d, c) for sid, (d, c) in acc.items()}


async def _load_benchmark_series(connection: Any) -> PriceSeries | None:
    """kospi20 벤치 = 유니버스 종목 종가의 일별 평균(지수 시계열이 없을 때 근사)."""
    rows = await connection.fetch(
        """
        SELECT trade_date, AVG(close) AS avg_close
        FROM ohlcv_data
        GROUP BY trade_date
        ORDER BY trade_date
        """
    )
    if not rows:
        return None
    return PriceSeries(
        [r["trade_date"] for r in rows], [float(r["avg_close"]) for r in rows]
    )
