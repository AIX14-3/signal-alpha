"""Search-vs-price timing (lead/lag) — the deterministic cause prelabel.

The DataLab cause agent (협업안 §4) asks: a search spike happened — is it a
*catalyst* (search ran ahead of price), *fomo* (search chased a price move that
already happened), or *price_led* (search merely tracked price)? That question is
fundamentally about *timing relative to price*, so this module derives it
deterministically from the search rows the analyzer already has plus the stock's
recent OHLCV close series. Pure and clock-free: ``as_of`` is supplied by the caller.

The result is a *prelabel* — the LLM classifier confirms it and writes the human
rationale; on LLM failure the agent falls back to this label. No buy/sell call:
this is a trace tag (docs §9).

⚠️ ``catalyst`` 를 방향 신호로 승격하지 말 것 (2026-07-13 실측, 3종목·2021~2026):
catalyst 시점의 20일 forward return 은 기준선 대비 +3.0%p 로 겉보기엔 유의(perm_p .0005)하나 —
  1. 겹치는 forward 창 때문에 121관측이 사실상 **독립표본 6개**뿐이다. 비겹침으로 재면
     t = −0.62 로 유의성이 사라진다.
  2. ``price_led``(**검색이 안 올랐는데** 최근 5% 이상 움직인 그룹)가 **+3.4%p 로 더 높다.**
     두 그룹의 유일한 차이가 검색인데 검색이 오른 쪽이 낮다 → 이 수익은 전부 "최근에 움직였다"
     (모멘텀)에서 나오고 **검색의 기여는 0(혹은 음)** 이다.
이 라벨은 과거 에피소드의 **서술**이지 예측이 아니다. 검색의 실증된 예측력은 방향이 아니라
매그니튜드 축(→ ``analyzers/datalab/attention.py``)에만 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.schemas.source_result import Cause

# A price move ≥ this fraction over half the window counts as "meaningful".
PRICE_MOVE_THRESHOLD = 0.05
# Search rising by more than this (recent vs prior avg) counts as "spiking".
SEARCH_RISE_THRESHOLD = 0.10
# Need at least this many price points to judge prior-vs-recent timing.
MIN_PRICE_POINTS = 3


@dataclass(frozen=True)
class LeadLag:
    search_recent_avg: float | None
    search_prior_avg: float | None
    search_momentum_pct: float | None
    price_prior_return: float | None
    price_recent_return: float | None
    price_points: int
    preliminary_cause: Cause | None
    note: str


def compute_lead_lag(
    search_rows: list[dict],
    price_rows: list[dict],
    *,
    as_of: date,
    lookback_days: int,
) -> LeadLag:
    """Derive search-vs-price timing and a deterministic cause prelabel.

    ``search_rows`` are the analyzer's DataLab rows (``observed_date``,
    ``search_index``, ``polarity``); ``price_rows`` are ``{"trade_date", "close"}``
    ascending by date. With too few price points the timing is undecidable, so
    ``preliminary_cause`` is None and the agent skips LLM cause classification.
    """
    midpoint = as_of - timedelta(days=max(1, lookback_days) // 2)

    search_recent_avg, search_prior_avg = _search_avgs(search_rows, midpoint)
    search_momentum = _ratio(search_recent_avg, search_prior_avg)

    closes = _ordered_closes(price_rows)
    if len(closes) < MIN_PRICE_POINTS:
        return LeadLag(
            search_recent_avg=search_recent_avg,
            search_prior_avg=search_prior_avg,
            search_momentum_pct=search_momentum,
            price_prior_return=None,
            price_recent_return=None,
            price_points=len(closes),
            preliminary_cause=None,
            note="가격 데이터 부족 — 검색-가격 선행/후행 판정 불가",
        )

    prior_return, recent_return = _split_returns(closes, midpoint)
    prelabel, note = _prelabel(search_momentum, prior_return, recent_return)
    return LeadLag(
        search_recent_avg=search_recent_avg,
        search_prior_avg=search_prior_avg,
        search_momentum_pct=search_momentum,
        price_prior_return=prior_return,
        price_recent_return=recent_return,
        price_points=len(closes),
        preliminary_cause=prelabel,
        note=note,
    )


def _moved(pct: float) -> bool:
    """의미 있는 가격 변동인가 — **부호 무관**(상승·하락 대칭).

    과거엔 세 분기 모두 ``>= PRICE_MOVE_THRESHOLD`` 였다. 그래서 검색이 선행한 **폭락**은
    catalyst/price_led 어디에도 안 잡히고 '모호'로 빠졌다 — 악재 급등(리콜·횡령·실적쇼크)에
    원인 태그가 영영 안 붙는 비대칭. cause 는 방향이 아니라 **타이밍**(누가 먼저였나)을 말하는
    축이므로 크기로만 판정해야 한다.
    """
    return abs(pct) >= PRICE_MOVE_THRESHOLD


def _dir(pct: float) -> str:
    return "상승" if pct > 0 else "하락"


def _prelabel(
    search_momentum: float | None,
    prior_return: float | None,
    recent_return: float | None,
) -> tuple[Cause | None, str]:
    if prior_return is None or recent_return is None:
        return None, "가격 수익률 산출 불가"
    rising = search_momentum is not None and search_momentum > SEARCH_RISE_THRESHOLD

    if rising and _moved(prior_return):
        # Price already moved in the prior window; search is chasing it now.
        return "fomo", (
            f"가격이 먼저 {prior_return * 100:+.0f}% {_dir(prior_return)}한 뒤 "
            f"검색이 따라붙음(FOMO 패턴)"
        )
    if rising and _moved(recent_return) and not _moved(prior_return):
        # Search rose while price was flat, then price followed.
        return "catalyst", (
            f"검색이 먼저 오르고 가격이 뒤따라 {recent_return * 100:+.0f}% "
            f"{_dir(recent_return)}(촉매 패턴)"
        )
    if _moved(recent_return) and not rising:
        # Price moving without a search rise: search merely tracks price.
        return "price_led", (
            f"검색 변화 없이 가격만 {recent_return * 100:+.0f}% 변동(가격 주도)"
        )
    return None, "검색-가격 타이밍이 뚜렷하지 않음(모호)"


def _search_avgs(rows: list[dict], midpoint: date) -> tuple[float | None, float | None]:
    recent_v = recent_w = prior_v = prior_w = 0.0
    for row in rows:
        if (row.get("polarity") or "demand") == "risk":
            continue  # demand-side attention only (mirror of indicators.py)
        observed = _parse_date(row.get("observed_date"))
        idx = row.get("search_index")
        if observed is None or idx is None:
            continue
        weight = float(row.get("weight") or 1.0)
        value = float(idx) * weight
        if observed > midpoint:
            recent_v += value
            recent_w += weight
        else:
            prior_v += value
            prior_w += weight
    recent = recent_v / recent_w if recent_w > 0 else None
    prior = prior_v / prior_w if prior_w > 0 else None
    return recent, prior


def _ordered_closes(price_rows: list[dict]) -> list[tuple[date, float]]:
    closes: list[tuple[date, float]] = []
    for row in price_rows:
        trade_date = _parse_date(row.get("trade_date"))
        close = row.get("close")
        if trade_date is None or close is None:
            continue
        closes.append((trade_date, float(close)))
    closes.sort(key=lambda item: item[0])
    return closes


def _split_returns(
    closes: list[tuple[date, float]],
    midpoint: date,
) -> tuple[float | None, float | None]:
    """Prior-window return (earliest → midpoint) and recent return (midpoint → latest)."""
    first_close = closes[0][1]
    last_close = closes[-1][1]
    # Close at/just-before the midpoint splits prior vs recent.
    mid_close = first_close
    for trade_date, close in closes:
        if trade_date <= midpoint:
            mid_close = close
        else:
            break
    prior_return = (mid_close - first_close) / first_close if first_close else None
    recent_return = (last_close - mid_close) / mid_close if mid_close else None
    return prior_return, recent_return


def _ratio(recent: float | None, prior: float | None) -> float | None:
    if recent is None or prior is None or prior == 0:
        return None
    return (recent - prior) / prior


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
