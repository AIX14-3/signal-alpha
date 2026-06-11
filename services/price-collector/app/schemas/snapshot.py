from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class TargetStock:
    stock_id: int
    ticker: str
    name: str


@dataclass(frozen=True)
class StockSnapshot:
    """Intraday snapshot of the kiwoom_data_spec OPT10001/ka10001 fields."""

    ticker: str
    captured_at: datetime
    trade_date: date
    current_price: Decimal
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    volume: int | None = None
    trade_value: int | None = None  # 거래대금 (백만원)
    market_cap: int | None = None  # 시가총액 (억원)
    shares_outstanding: int | None = None  # 상장주수 (천주)
    per: Decimal | None = None
    pbr: Decimal | None = None
    eps: Decimal | None = None
    bps: Decimal | None = None
    roe: Decimal | None = None
    roa: Decimal | None = None

    def has_full_ohlc(self) -> bool:
        return None not in (self.open, self.high, self.low) and self.volume is not None


@dataclass(frozen=True)
class InvestorFlow:
    """Daily investor net-buy figures (kiwoom_data_spec OPT10059/ka10059)."""

    ticker: str
    trade_date: date
    individual_net: int | None = None
    foreign_net: int | None = None
    institution_net: int | None = None
