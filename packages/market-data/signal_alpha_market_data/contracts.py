"""소스 중립 DTO + MarketDataSource 프로토콜.

기존 price collector의 `StockSnapshot`(services/agent-worker/app/collectors/price/schemas.py)
필드 의미를 그대로 따른다. 단위 주석을 보존해 소스 간 매핑 시 혼선을 막는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ValuationMetrics:
    """종목 밸류에이션 지표. PER/PBR/EPS/BPS/ROE/ROA는 소스(키움) 직값,
    PSR은 파생 계산값(시총÷매출 TTM). 값이 없으면 None."""

    ticker: str
    per: Decimal | None = None
    pbr: Decimal | None = None
    psr: Decimal | None = None
    eps: Decimal | None = None
    bps: Decimal | None = None
    roe: Decimal | None = None
    roa: Decimal | None = None


@dataclass(frozen=True)
class PriceSnapshot:
    """시점 시세 스냅샷. 단위는 키움 ka10001 기준."""

    ticker: str
    captured_at: datetime
    current_price: Decimal
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    volume: int | None = None  # 주
    trade_value: int | None = None  # 거래대금 (백만원)
    market_cap: int | None = None  # 시가총액 (억원)
    shares_outstanding: int | None = None  # 상장주수 (천주)
    currency: str = "KRW"


@dataclass(frozen=True)
class Candle:
    """일/주/월봉 1개."""

    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None = None
    currency: str = "KRW"


@dataclass(frozen=True)
class ExchangeRate:
    base_currency: str
    quote_currency: str
    rate: Decimal
    as_of: datetime | None = None


@runtime_checkable
class MarketDataSource(Protocol):
    """시장 데이터 소스 계약. 소스가 지원하지 않는 메서드는 NotImplementedError를 던진다
    (예: 토스는 get_valuation 미지원, 키움은 get_exchange_rate 미지원)."""

    name: str

    async def get_price_snapshot(self, ticker: str) -> PriceSnapshot: ...

    async def get_daily_candles(
        self, symbol: str, start: date, end: date | None = None
    ) -> list[Candle]: ...

    async def get_valuation(self, ticker: str) -> ValuationMetrics: ...

    async def get_exchange_rate(
        self, base: str = "USD", quote: str = "KRW"
    ) -> ExchangeRate: ...

    async def get_us_quote(self, symbol: str) -> PriceSnapshot: ...
