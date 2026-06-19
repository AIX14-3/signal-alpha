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


def _prelabel(
    search_momentum: float | None,
    prior_return: float | None,
    recent_return: float | None,
) -> tuple[Cause | None, str]:
    if prior_return is None or recent_return is None:
        return None, "가격 수익률 산출 불가"
    rising = search_momentum is not None and search_momentum > SEARCH_RISE_THRESHOLD

    if rising and prior_return >= PRICE_MOVE_THRESHOLD:
        # Price already jumped in the prior window; search is chasing it now.
        return "fomo", (
            f"가격이 먼저 {prior_return * 100:+.0f}% 상승한 뒤 검색이 따라붙음(FOMO 패턴)"
        )
    if rising and recent_return >= PRICE_MOVE_THRESHOLD and prior_return < PRICE_MOVE_THRESHOLD:
        # Search rose while price was flat, then price followed: search led.
        return "catalyst", (
            f"검색이 먼저 오르고 가격이 뒤따라 {recent_return * 100:+.0f}% 상승(촉매 패턴)"
        )
    if recent_return >= PRICE_MOVE_THRESHOLD and not rising:
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
