"""asyncpg persistence for the realtime price collector.

Targets come from the shared ``stocks`` table (``is_target`` switch — no
hardcoded ticker lists). Snapshots land in ``price_snapshots``; the latest
snapshot also keeps today's ``ohlcv_data`` row fresh so the PRICE analyzer
reads current figures. Investor-flow columns on ``ohlcv_data`` are only
touched by the dedicated after-close update, never by intraday upserts.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.collectors.price.schemas import InvestorFlow, StockSnapshot, TargetStock

_INSERT_SNAPSHOT = """
INSERT INTO price_snapshots (
    stock_id, captured_at, trade_date, current_price, open, high, low,
    volume, trade_value, market_cap, shares_outstanding,
    per, pbr, eps, bps, roe, roa
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
ON CONFLICT (stock_id, captured_at) DO NOTHING
"""

# Intraday upsert: OHLCV columns refresh every poll; investor-flow and
# market-cap columns are preserved unless the new value is non-null
# (same COALESCE pattern the C-4 dedup review recommended).
_UPSERT_INTRADAY_OHLCV = """
INSERT INTO ohlcv_data (stock_id, trade_date, open, high, low, close, volume, market_cap)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (stock_id, trade_date) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    market_cap = COALESCE(EXCLUDED.market_cap, ohlcv_data.market_cap)
"""

_UPDATE_FLOWS = """
UPDATE ohlcv_data
SET foreign_net = COALESCE($3, foreign_net),
    institution_net = COALESCE($4, institution_net)
WHERE stock_id = $1 AND trade_date = $2
"""


class PriceSnapshotRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def list_target_stocks(self) -> list[TargetStock]:
        rows = await self._connection.fetch(
            """
            SELECT id, ticker, name
            FROM stocks
            WHERE is_target = TRUE AND is_active = TRUE
            ORDER BY ticker
            """
        )
        return [TargetStock(stock_id=r["id"], ticker=r["ticker"], name=r["name"]) for r in rows]

    async def insert_snapshot(self, stock_id: int, snapshot: StockSnapshot) -> None:
        await self._connection.execute(
            _INSERT_SNAPSHOT,
            stock_id,
            snapshot.captured_at,
            snapshot.trade_date,
            snapshot.current_price,
            snapshot.open,
            snapshot.high,
            snapshot.low,
            snapshot.volume,
            snapshot.trade_value,
            snapshot.market_cap,
            snapshot.shares_outstanding,
            snapshot.per,
            snapshot.pbr,
            snapshot.eps,
            snapshot.bps,
            snapshot.roe,
            snapshot.roa,
        )

    async def upsert_intraday_ohlcv(self, stock_id: int, snapshot: StockSnapshot) -> bool:
        """Refresh today's ohlcv_data row from the snapshot.

        Skipped (returns False) when the snapshot lacks full OHLC values —
        ohlcv_data columns are NOT NULL, so a partial row cannot be written.
        """
        if not snapshot.has_full_ohlc():
            return False
        await self._connection.execute(
            _UPSERT_INTRADAY_OHLCV,
            stock_id,
            snapshot.trade_date,
            snapshot.open,
            snapshot.high,
            snapshot.low,
            snapshot.current_price,
            snapshot.volume,
            snapshot.market_cap,
        )
        return True

    async def update_investor_flow(self, stock_id: int, flow: InvestorFlow) -> bool:
        """Attach confirmed net-buy figures to an existing ohlcv_data row."""
        result = await self._connection.execute(
            _UPDATE_FLOWS,
            stock_id,
            flow.trade_date,
            flow.foreign_net,
            flow.institution_net,
        )
        return _rows_affected(result) > 0


def _rows_affected(command_tag: str) -> int:
    # asyncpg returns command tags like "UPDATE 1".
    try:
        return int(str(command_tag).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def latest_trading_date_with_rows(connection: Any, stock_id: int) -> date | None:
    row = await connection.fetchrow(
        "SELECT MAX(trade_date) AS latest FROM ohlcv_data WHERE stock_id = $1",
        stock_id,
    )
    return row["latest"] if row else None
