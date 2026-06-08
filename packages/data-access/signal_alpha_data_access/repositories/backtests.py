from __future__ import annotations

from typing import Any


class BacktestRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def create_result(
        self,
        *,
        final_signal_id: int,
        stock_id: int,
        signal_date: Any,
        signal_score: Any,
        signal_value: str,
        price_at_signal: Any,
        confidence_band: str | None = None,
        source_agreement: str | None = None,
    ) -> int:
        return await self._connection.fetchval(
            """
            INSERT INTO backtest_results (
                final_signal_id, stock_id, signal_date, signal_score,
                signal_value, price_at_signal, confidence_band, source_agreement
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            final_signal_id,
            stock_id,
            signal_date,
            signal_score,
            signal_value,
            price_at_signal,
            confidence_band,
            source_agreement,
        )

    async def list_pending_checks(self, limit: int = 100) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT *
            FROM backtest_results
            WHERE checked_at IS NULL
            ORDER BY signal_date ASC, id ASC
            LIMIT $1
            """,
            limit,
        )

    async def list_by_stock(self, *, stock_id: int, limit: int = 100) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT *
            FROM backtest_results
            WHERE stock_id = $1
            ORDER BY signal_date DESC, id DESC
            LIMIT $2
            """,
            stock_id,
            limit,
        )

    async def complete_result(
        self,
        *,
        result_id: int,
        price_after_5d: Any,
        change_pct_5d: Any,
        is_hit: bool,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            UPDATE backtest_results
            SET
                price_after_5d = $2,
                change_pct_5d = $3,
                checked_at = NOW(),
                is_hit = $4
            WHERE id = $1
            RETURNING *
            """,
            result_id,
            price_after_5d,
            change_pct_5d,
            is_hit,
        )
