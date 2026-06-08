"""Normalized price-domain records and the merge into ``ohlcv_data`` rows.

These dataclasses carry collected-but-not-analyzed values only. No direction,
score, or summary is produced here (collectors stay analysis-free).
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DailyCandle:
    """One OHLCV row from OPT10081 (주식 일봉 차트)."""

    trade_date: date
    open: int
    high: int
    low: int
    close: int
    volume: int
    trading_value: int | None = None  # 거래대금, 백만원


@dataclass(frozen=True)
class InvestorFlow:
    """One row of supply/demand from OPT10059 (투자자 매매동향)."""

    trade_date: date
    individual_net: int
    foreign_net: int
    institution_net: int
    foreign_holding: int | None = None
    foreign_holding_pct: float | None = None


@dataclass(frozen=True)
class StockBasic:
    """Snapshot from OPT10001 (주식 기본 정보), valid for the latest session."""

    ticker: str
    close: int
    market_cap: int | None = None      # 시가총액, 억원 (as provided by Kiwoom)
    listed_shares: int | None = None   # 상장주수, 천주
    per: float | None = None
    pbr: float | None = None
    eps: float | None = None
    bps: float | None = None
    roe: float | None = None
    roa: float | None = None


@dataclass(frozen=True)
class OhlcvRow:
    """A row ready to upsert into the shared ``ohlcv_data`` table."""

    ticker: str
    trade_date: date
    open: int
    high: int
    low: int
    close: int
    volume: int
    foreign_net: int | None = None
    institution_net: int | None = None
    change_pct: float | None = None
    market_cap: int | None = None


def build_ohlcv_rows(
    ticker: str,
    candles: list[DailyCandle],
    flows: list[InvestorFlow] | None = None,
    basic: StockBasic | None = None
) -> list[OhlcvRow]:
    """Combine daily candles, investor flows, and the basic snapshot.

    ``change_pct`` is derived from the prior session's close. ``market_cap``
    only applies to the most recent trade date (the snapshot is point-in-time).
    """
    flow_by_date = {flow.trade_date: flow for flow in (flows or [])}
    ordered = sorted(candles, key=lambda candle: candle.trade_date)
    latest_date = ordered[-1].trade_date if ordered else None

    rows: list[OhlcvRow] = []
    prev_close: int | None = None
    for candle in ordered:
        flow = flow_by_date.get(candle.trade_date)
        change_pct: float | None = None
        if prev_close:
            change_pct = round((candle.close - prev_close) / prev_close * 100, 2)
        market_cap = (
            basic.market_cap
            if basic is not None and candle.trade_date == latest_date
            else None
        )
        rows.append(
            OhlcvRow(
                ticker=ticker,
                trade_date=candle.trade_date,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                foreign_net=flow.foreign_net if flow else None,
                institution_net=flow.institution_net if flow else None,
                change_pct=change_pct,
                market_cap=market_cap
            )
        )
        prev_close = candle.close
    return rows
