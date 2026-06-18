from __future__ import annotations

from typing import Any


class UserSignalRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def add_watchlist(
        self,
        *,
        user_id: int,
        stock_id: int,
        notification_enabled: bool = False,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO watchlists (user_id, stock_id, notification_enabled)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, stock_id)
            DO UPDATE SET notification_enabled = EXCLUDED.notification_enabled
            RETURNING *
            """,
            user_id,
            stock_id,
            notification_enabled,
        )

    async def count_watchlist(self, *, user_id: int) -> int:
        return int(
            await self._connection.fetchval(
                """
                SELECT COUNT(*)
                FROM watchlists
                WHERE user_id = $1
                """,
                user_id,
            )
        )

    async def get_watchlist_item(self, *, user_id: int, stock_id: int) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT
                watchlists.*,
                stocks.ticker,
                stocks.name,
                stocks.market,
                stocks.sector
            FROM watchlists
            INNER JOIN stocks
                ON stocks.id = watchlists.stock_id
            WHERE watchlists.user_id = $1
              AND watchlists.stock_id = $2
            """,
            user_id,
            stock_id,
        )

    async def remove_watchlist(self, *, user_id: int, stock_id: int) -> None:
        await self._connection.execute(
            """
            DELETE FROM watchlists
            WHERE user_id = $1
              AND stock_id = $2
            """,
            user_id,
            stock_id,
        )

    async def list_watchlist(self, *, user_id: int) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                watchlists.*,
                stocks.ticker,
                stocks.name,
                stocks.market,
                stocks.sector
            FROM watchlists
            INNER JOIN stocks
                ON stocks.id = watchlists.stock_id
            WHERE watchlists.user_id = $1
            ORDER BY watchlists.created_at DESC
            """,
            user_id,
        )

    async def mark_signal_read(self, *, user_id: int, final_signal_id: int) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO user_signal_reads (user_id, final_signal_id)
            VALUES ($1, $2)
            ON CONFLICT (user_id, final_signal_id)
            DO UPDATE SET read_at = NOW(), read_date = CURRENT_DATE
            RETURNING *
            """,
            user_id,
            final_signal_id,
        )

    async def create_journal(
        self,
        *,
        user_id: int,
        stock_id: int,
        user_view: str,
        final_signal_id: int | None = None,
        user_memo: str | None = None,
        decision_type: str | None = None,
        decision_reason: str | None = None,
        signal_score_at_time: Any | None = None,
        signal_value_at_time: str | None = None,
        price_at_time: Any | None = None,
        source_agreement_at_time: str | None = None,
    ) -> int:
        return await self._connection.fetchval(
            """
            INSERT INTO signal_journals (
                user_id, final_signal_id, stock_id, user_view, user_memo,
                decision_type, decision_reason, signal_score_at_time,
                signal_value_at_time, price_at_time, source_agreement_at_time
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id
            """,
            user_id,
            final_signal_id,
            stock_id,
            user_view,
            user_memo,
            decision_type,
            decision_reason,
            signal_score_at_time,
            signal_value_at_time,
            price_at_time,
            source_agreement_at_time,
        )

    async def update_journal_outcome(
        self,
        *,
        journal_id: int,
        outcome_price: Any,
        outcome_change_pct: Any,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            UPDATE signal_journals
            SET
                outcome_price = $2,
                outcome_change_pct = $3,
                outcome_checked_at = NOW(),
                updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            journal_id,
            outcome_price,
            outcome_change_pct,
        )
