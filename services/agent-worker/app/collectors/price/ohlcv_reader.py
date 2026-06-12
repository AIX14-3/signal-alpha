"""Read collected price history from the shared database.

Per docs/architecture.md the analysis side never calls the Kiwoom API directly:
the price collection daemon (``price/runner.py``) writes `ohlcv_data`, and this
collector only reads those rows back. The numeric series rides in ``RawEvidence.metadata["rows"]``
so the analyzer stays free of database concerns.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Protocol

from app.schemas.evidence import RawEvidence

DEFAULT_LOOKBACK_SESSIONS = 120
# A listed stock should have traded within the last week; older data is stale.
STALE_AFTER_DAYS = 7


class StockLookupRepository(Protocol):
    async def get_by_ticker(self, ticker: str) -> Any:
        pass


class OhlcvRepository(Protocol):
    async def list_recent_ohlcv(self, *, stock_id: int, limit: int = 120) -> list[Any]:
        pass


class OhlcvReader:
    source = "PRICE"

    def __init__(
        self,
        *,
        stocks: StockLookupRepository,
        market_data: OhlcvRepository,
        lookback_sessions: int = DEFAULT_LOOKBACK_SESSIONS,
        today: date | None = None,
    ) -> None:
        self._stocks = stocks
        self._market_data = market_data
        self._lookback_sessions = lookback_sessions
        self._today = today

    async def collect(self, stock_code: str) -> list[RawEvidence]:
        stock = await self._stocks.get_by_ticker(stock_code)
        if stock is None:
            return []

        records = await self._market_data.list_recent_ohlcv(
            stock_id=stock["id"],
            limit=self._lookback_sessions,
        )
        if not records:
            return []

        rows = [_normalize_row(record) for record in records]
        latest_trade_date = rows[-1]["trade_date"]
        return [
            RawEvidence(
                source="PRICE",
                stock_code=stock_code,
                title=f"주가/수급 시계열 (최근 {len(rows)}영업일)",
                content=(
                    f"ohlcv_data {rows[0]['trade_date']} ~ {latest_trade_date}, "
                    f"{len(rows)}개 세션"
                ),
                published_at=latest_trade_date,
                metadata={
                    "rows": rows,
                    "lookback_sessions": self._lookback_sessions,
                    "latest_trade_date": latest_trade_date,
                    "stale": self._is_stale(rows[-1]),
                    "stock_name": stock["name"],
                },
            )
        ]

    def _is_stale(self, latest_row: dict[str, Any]) -> bool:
        today = self._today or date.today()
        latest = date.fromisoformat(latest_row["trade_date"])
        return today - latest > timedelta(days=STALE_AFTER_DAYS)


def _normalize_row(record: Any) -> dict[str, Any]:
    """Flatten a DB record into JSON-safe primitives (Decimal→float, date→str)."""
    trade_date = record["trade_date"]
    return {
        "trade_date": trade_date.isoformat() if isinstance(trade_date, date) else str(trade_date),
        "open": float(record["open"]),
        "high": float(record["high"]),
        "low": float(record["low"]),
        "close": float(record["close"]),
        "volume": int(record["volume"]),
        "foreign_net": _opt_int(record, "foreign_net"),
        "institution_net": _opt_int(record, "institution_net"),
        "change_pct": _opt_float(record, "change_pct"),
        "market_cap": _opt_int(record, "market_cap"),
    }


def _opt_int(record: Any, key: str) -> int | None:
    value = record[key]
    return None if value is None else int(value)


def _opt_float(record: Any, key: str) -> float | None:
    value = record[key]
    return None if value is None else float(value)
