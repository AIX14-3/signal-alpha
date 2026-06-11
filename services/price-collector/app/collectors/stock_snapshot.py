"""ka10001 주식기본정보 — intraday snapshot collector.

Field names follow the Kiwoom REST API document for ka10001. They are kept in
one map so a mismatch found during live verification against the mock domain
is a one-line fix.
"""

from __future__ import annotations

from datetime import datetime

from app.kiwoom.parsing import parse_decimal, parse_int, parse_price
from app.kiwoom.rest_client import KiwoomRestClient
from app.schemas.snapshot import StockSnapshot

API_ID = "ka10001"

# spec field -> Kiwoom REST response key
KA10001_FIELDS = {
    "current_price": "cur_prc",  # 현재가 (원)
    "open": "open_pric",  # 시가 (원)
    "high": "high_pric",  # 고가 (원)
    "low": "low_pric",  # 저가 (원)
    "volume": "trde_qty",  # 거래량 (주)
    "trade_value": "trde_prica",  # 거래대금 (백만원)
    "market_cap": "mac",  # 시가총액 (억원)
    "shares_outstanding": "flo_stk",  # 상장주수 (천주)
    "per": "per",
    "pbr": "pbr",
    "eps": "eps",
    "bps": "bps",
    "roe": "roe",
    "roa": "roa",
}


class SnapshotUnavailableError(RuntimeError):
    """Raised when the response carries no usable current price."""


async def fetch_stock_snapshot(
    client: KiwoomRestClient, ticker: str, captured_at: datetime
) -> StockSnapshot:
    payload = await client.request(API_ID, {"stk_cd": ticker})

    current_price = parse_price(payload.get(KA10001_FIELDS["current_price"]))
    if current_price is None:
        raise SnapshotUnavailableError(f"{ticker}: ka10001 returned no current price")

    return StockSnapshot(
        ticker=ticker,
        captured_at=captured_at,
        trade_date=captured_at.date(),
        current_price=current_price,
        open=parse_price(payload.get(KA10001_FIELDS["open"])),
        high=parse_price(payload.get(KA10001_FIELDS["high"])),
        low=parse_price(payload.get(KA10001_FIELDS["low"])),
        volume=parse_int(payload.get(KA10001_FIELDS["volume"])),
        trade_value=parse_int(payload.get(KA10001_FIELDS["trade_value"])),
        market_cap=parse_int(payload.get(KA10001_FIELDS["market_cap"])),
        shares_outstanding=parse_int(payload.get(KA10001_FIELDS["shares_outstanding"])),
        per=parse_decimal(payload.get(KA10001_FIELDS["per"])),
        pbr=parse_decimal(payload.get(KA10001_FIELDS["pbr"])),
        eps=parse_decimal(payload.get(KA10001_FIELDS["eps"])),
        bps=parse_decimal(payload.get(KA10001_FIELDS["bps"])),
        roe=parse_decimal(payload.get(KA10001_FIELDS["roe"])),
        roa=parse_decimal(payload.get(KA10001_FIELDS["roa"])),
    )
