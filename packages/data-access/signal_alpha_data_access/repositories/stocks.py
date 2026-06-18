from __future__ import annotations

from typing import Any


class StockRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def get_by_ticker(self, ticker: str) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT id, ticker, name, market, sector, is_active, created_at, updated_at
            FROM stocks
            WHERE ticker = $1
            """,
            ticker.strip(),
        )

    async def list_active(self, limit: int = 100) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT id, ticker, name, market, sector, is_active, created_at, updated_at
            FROM stocks
            WHERE is_active = TRUE
            ORDER BY market ASC, ticker ASC
            LIMIT $1
            """,
            limit,
        )

    async def search_active(self, query: str, limit: int = 20) -> list[Any]:
        pattern = f"%{query.strip()}%"
        return await self._connection.fetch(
            """
            SELECT id, ticker, name, market, sector, is_active, created_at, updated_at
            FROM stocks
            WHERE is_active = TRUE
              AND (
                  ticker ILIKE $1
                  OR name ILIKE $1
              )
            ORDER BY market ASC, ticker ASC
            LIMIT $2
            """,
            pattern,
            limit,
        )

    async def ensure_stock(
        self,
        *,
        ticker: str,
        name: str,
        market: str,
        sector: str | None = None,
        is_active: bool = True,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO stocks (ticker, name, market, sector, is_active)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (ticker)
            DO UPDATE SET
                name = EXCLUDED.name,
                market = EXCLUDED.market,
                sector = EXCLUDED.sector,
                is_active = EXCLUDED.is_active,
                updated_at = NOW()
            RETURNING id, ticker, name, market, sector, is_active, created_at, updated_at
            """,
            ticker.strip(),
            name,
            market,
            sector,
            is_active,
        )
