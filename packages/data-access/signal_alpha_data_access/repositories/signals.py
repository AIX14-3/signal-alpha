from __future__ import annotations

from typing import Any


class SignalRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def get_current_by_ticker(self, ticker: str) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT
                final_signals.*,
                stocks.ticker,
                stocks.name,
                stocks.market,
                analysis_results.analysis_mode,
                analysis_results.base_score,
                analysis_results.warning AS analysis_warning
            FROM final_signals
            INNER JOIN stocks
                ON stocks.id = final_signals.stock_id
            INNER JOIN analysis_results
                ON analysis_results.id = final_signals.analysis_result_id
            WHERE stocks.ticker = $1
              AND final_signals.is_current = TRUE
              AND final_signals.is_published = TRUE
            ORDER BY final_signals.published_at DESC NULLS LAST,
                     final_signals.created_at DESC
            LIMIT 1
            """,
            ticker.strip(),
        )

    async def list_current_published(self, limit: int = 50) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                final_signals.*,
                stocks.ticker,
                stocks.name,
                stocks.market
            FROM final_signals
            INNER JOIN stocks
                ON stocks.id = final_signals.stock_id
            WHERE final_signals.is_current = TRUE
              AND final_signals.is_published = TRUE
            ORDER BY final_signals.published_at DESC NULLS LAST,
                     final_signals.created_at DESC
            LIMIT $1
            """,
            limit,
        )

    async def list_current_by_stock_ids(self, stock_ids: list[int]) -> list[Any]:
        if not stock_ids:
            return []
        return await self._connection.fetch(
            """
            SELECT
                final_signals.*,
                stocks.ticker,
                stocks.name,
                stocks.market
            FROM final_signals
            INNER JOIN stocks
                ON stocks.id = final_signals.stock_id
            WHERE final_signals.stock_id = ANY($1::BIGINT[])
              AND final_signals.is_current = TRUE
              AND final_signals.is_published = TRUE
            ORDER BY final_signals.stock_id ASC,
                     final_signals.published_at DESC NULLS LAST,
                     final_signals.created_at DESC
            """,
            stock_ids,
        )
