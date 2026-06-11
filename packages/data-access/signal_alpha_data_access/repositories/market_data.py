from __future__ import annotations

from typing import Any


class MarketDataRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_ohlcv(
        self,
        *,
        stock_id: int,
        trade_date: Any,
        open_price: Any,
        high_price: Any,
        low_price: Any,
        close_price: Any,
        volume: int,
        adjusted_close: Any | None = None,
        foreign_net: int | None = None,
        institution_net: int | None = None,
        change_pct: Any | None = None,
        market_cap: int | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO ohlcv_data (
                stock_id, trade_date, open, high, low, close, volume,
                adjusted_close, foreign_net, institution_net, change_pct, market_cap
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (stock_id, trade_date)
            DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                adjusted_close = EXCLUDED.adjusted_close,
                foreign_net = EXCLUDED.foreign_net,
                institution_net = EXCLUDED.institution_net,
                change_pct = EXCLUDED.change_pct,
                market_cap = EXCLUDED.market_cap
            RETURNING *
            """,
            stock_id,
            trade_date,
            open_price,
            high_price,
            low_price,
            close_price,
            volume,
            adjusted_close,
            foreign_net,
            institution_net,
            change_pct,
            market_cap,
        )

    async def list_ohlcv_between(
        self,
        *,
        stock_id: int,
        start_date: Any,
        end_date: Any,
    ) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT *
            FROM ohlcv_data
            WHERE stock_id = $1
              AND trade_date BETWEEN $2 AND $3
            ORDER BY trade_date ASC
            """,
            stock_id,
            start_date,
            end_date,
        )

    async def list_recent_ohlcv(self, *, stock_id: int, limit: int = 120) -> list[Any]:
        """Latest ``limit`` sessions, oldest first (subquery keeps the LIMIT on the newest rows)."""
        return await self._connection.fetch(
            """
            SELECT *
            FROM (
                SELECT *
                FROM ohlcv_data
                WHERE stock_id = $1
                ORDER BY trade_date DESC
                LIMIT $2
            ) recent
            ORDER BY trade_date ASC
            """,
            stock_id,
            limit,
        )

    async def get_price_on_or_after(self, *, stock_id: int, trade_date: Any) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM ohlcv_data
            WHERE stock_id = $1
              AND trade_date >= $2
            ORDER BY trade_date ASC
            LIMIT 1
            """,
            stock_id,
            trade_date,
        )

    async def upsert_fundamental(
        self,
        *,
        stock_id: int,
        fiscal_date: Any,
        period_type: str,
        revenue: int | None = None,
        net_income: int | None = None,
        operating_margin: Any | None = None,
        eps: Any | None = None,
        bps: Any | None = None,
        per: Any | None = None,
        pbr: Any | None = None,
        roe: Any | None = None,
        roa: Any | None = None,
        debt_ratio: Any | None = None,
        source: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO fundamentals (
                stock_id, fiscal_date, period_type, revenue, net_income,
                operating_margin, eps, bps, per, pbr, roe, roa, debt_ratio, source
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            ON CONFLICT (stock_id, fiscal_date, period_type)
            DO UPDATE SET
                revenue = EXCLUDED.revenue,
                net_income = EXCLUDED.net_income,
                operating_margin = EXCLUDED.operating_margin,
                eps = EXCLUDED.eps,
                bps = EXCLUDED.bps,
                per = EXCLUDED.per,
                pbr = EXCLUDED.pbr,
                roe = EXCLUDED.roe,
                roa = EXCLUDED.roa,
                debt_ratio = EXCLUDED.debt_ratio,
                source = EXCLUDED.source
            RETURNING *
            """,
            stock_id,
            fiscal_date,
            period_type,
            revenue,
            net_income,
            operating_margin,
            eps,
            bps,
            per,
            pbr,
            roe,
            roa,
            debt_ratio,
            source,
        )
