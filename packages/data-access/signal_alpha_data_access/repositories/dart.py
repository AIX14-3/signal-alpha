from __future__ import annotations

from datetime import date, datetime
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

    async def get_collection_state_by_ticker(self, ticker: str) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM dart_collection_states
            WHERE ticker = $1
            """,
            ticker.strip(),
        )

    async def upsert_collection_state(
        self,
        *,
        stock_id: int,
        ticker: str,
        last_bgn_de: str,
        last_end_de: str,
        last_receipt_no: str | None = None,
        last_collected_count: int = 0,
        last_collector_run_id: int | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO dart_collection_states (
                stock_id,
                ticker,
                last_bgn_de,
                last_end_de,
                last_receipt_no,
                last_collected_count,
                last_collector_run_id
            )
            VALUES ($1, $2, $3::DATE, $4::DATE, $5, $6, $7)
            ON CONFLICT (stock_id)
            DO UPDATE SET
                ticker = EXCLUDED.ticker,
                last_bgn_de = EXCLUDED.last_bgn_de,
                last_end_de = EXCLUDED.last_end_de,
                last_receipt_no = EXCLUDED.last_receipt_no,
                last_collected_count = EXCLUDED.last_collected_count,
                last_collector_run_id = EXCLUDED.last_collector_run_id,
                updated_at = NOW()
            RETURNING *
            """,
            stock_id,
            ticker.strip(),
            _to_date(last_bgn_de),
            _to_date(last_end_de),
            last_receipt_no,
            last_collected_count,
            last_collector_run_id,
        )

    async def upsert_listed_corp_codes(self, entries: list[dict[str, Any]]) -> int:
        rows = [
            (
                entry["corp_code"].strip(),
                entry["ticker"].strip(),
                entry["corp_name"],
                entry.get("stock_name") or entry["corp_name"],
                True,
            )
            for entry in entries
            if entry.get("corp_code") and entry.get("ticker") and entry.get("corp_name")
        ]
        if not rows:
            return 0

        await self._connection.executemany(
            """
            INSERT INTO dart_corp_codes (
                stock_id,
                corp_code,
                ticker,
                corp_name,
                stock_name,
                is_active,
                synced_at
            )
            VALUES (
                (SELECT id FROM stocks WHERE ticker = $2),
                $1,
                $2,
                $3,
                $4,
                $5,
                NOW()
            )
            ON CONFLICT (ticker)
            DO UPDATE SET
                stock_id = EXCLUDED.stock_id,
                corp_code = EXCLUDED.corp_code,
                corp_name = EXCLUDED.corp_name,
                stock_name = EXCLUDED.stock_name,
                is_active = EXCLUDED.is_active,
                synced_at = EXCLUDED.synced_at,
                updated_at = NOW()
            """,
            rows,
        )
        return len(rows)


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return datetime.fromisoformat(text[:10]).date()
