"""Normalized sector (업종) index records and the merge into ``sector_ohlcv`` rows.

Sector indices are expressed in points (pt) with two decimals, so OHLC values
are floats rather than the integer won prices used for stocks. Derived metrics
(상대강도, 12개월 수익률 등) are not produced here — those are computed
downstream in ``signal_metrics``.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SectorRef:
    """A sector row resolved from the ``sectors`` table."""

    id: int
    kiwoom_code: str
    market: str


@dataclass(frozen=True)
class SectorDailyCandle:
    """One OHLCV row from OPT20006 (업종 일봉 차트)."""

    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None
    trading_value: int | None = None  # 거래대금, 백만원


@dataclass(frozen=True)
class SectorQuote:
    """Snapshot from OPT20004 (업종별 현재 시세)."""

    name: str | None
    close: float                  # 현재가 (업종지수), pt
    change: float | None = None   # 전일대비, pt
    change_pct: float | None = None  # 등락률, %
    volume: int | None = None
    trading_value: int | None = None


@dataclass(frozen=True)
class SectorOhlcvRow:
    """A row ready to upsert into ``sector_ohlcv``."""

    sector_id: int
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None
    trading_value: int | None = None
    change_pct: float | None = None


def build_sector_ohlcv_rows(
    sector_id: int,
    candles: list[SectorDailyCandle]
) -> list[SectorOhlcvRow]:
    """Order candles oldest-first and derive ``change_pct`` from prior close."""
    ordered = sorted(candles, key=lambda candle: candle.trade_date)
    rows: list[SectorOhlcvRow] = []
    prev_close: float | None = None
    for candle in ordered:
        change_pct: float | None = None
        if prev_close:
            change_pct = round((candle.close - prev_close) / prev_close * 100, 2)
        rows.append(
            SectorOhlcvRow(
                sector_id=sector_id,
                trade_date=candle.trade_date,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                trading_value=candle.trading_value,
                change_pct=change_pct
            )
        )
        prev_close = candle.close
    return rows
