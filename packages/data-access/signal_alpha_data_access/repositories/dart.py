from __future__ import annotations

from typing import Any


class DartRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_corp_code(
        self,
        *,
        corp_code: str,
        ticker: str,
        corp_name: str,
        stock_id: int | None = None,
        corp_name_eng: str | None = None,
        stock_name: str | None = None,
        is_active: bool = True,
        synced_at: Any | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO dart_corp_codes (
                stock_id,
                corp_code,
                ticker,
                corp_name,
                corp_name_eng,
                stock_name,
                is_active,
                synced_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, COALESCE($8, NOW()))
            ON CONFLICT (corp_code)
            DO UPDATE SET
                stock_id = EXCLUDED.stock_id,
                ticker = EXCLUDED.ticker,
                corp_name = EXCLUDED.corp_name,
                corp_name_eng = EXCLUDED.corp_name_eng,
                stock_name = EXCLUDED.stock_name,
                is_active = EXCLUDED.is_active,
                synced_at = EXCLUDED.synced_at,
                updated_at = NOW()
            RETURNING *
            """,
            stock_id,
            corp_code.strip(),
            ticker.strip(),
            corp_name,
            corp_name_eng,
            stock_name,
            is_active,
            synced_at,
        )

    async def get_corp_code_by_ticker(self, ticker: str) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM dart_corp_codes
            WHERE ticker = $1
              AND is_active = TRUE
            """,
            ticker.strip(),
        )

    async def get_corp_code(self, corp_code: str) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM dart_corp_codes
            WHERE corp_code = $1
              AND is_active = TRUE
            """,
            corp_code.strip(),
        )
