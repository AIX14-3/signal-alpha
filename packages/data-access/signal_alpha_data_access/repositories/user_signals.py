from __future__ import annotations

import json
from typing import Any

# backend 전용 repository. watchlists/signal_journals/user_signal_reads 는 backend 소유
# 테이블이라 base 에 직접 쓰지만, 종목 마스터 조인은 worker 소유 stocks 대신 api.stocks
# 읽기 view 를 사용한다(backend 롤은 base stocks 권한 없음, api.stocks SELECT 만 보유).


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
            INNER JOIN api.stocks
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
            INNER JOIN api.stocks
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

    async def create_journal_entry(
        self,
        *,
        user_id: int,
        stock_id: int,
        final_signal_id: int,
        user_view: str,
        user_memo: str | None = None,
        tags: list[str] | None = None,
        signal_score_at_time: Any | None = None,
        signal_value_at_time: str | None = None,
        source_agreement_at_time: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            WITH inserted AS (
                INSERT INTO signal_journals (
                    user_id, final_signal_id, stock_id, user_view, user_memo,
                    tags, signal_score_at_time, signal_value_at_time,
                    source_agreement_at_time
                )
                VALUES ($1, $2, $3, $4, $5, $6::JSONB, $7, $8, $9)
                RETURNING *
            )
            SELECT
                inserted.*,
                stocks.ticker,
                stocks.name,
                stocks.market,
                stocks.sector
            FROM inserted
            INNER JOIN api.stocks
                ON stocks.id = inserted.stock_id
            """,
            user_id,
            final_signal_id,
            stock_id,
            user_view,
            user_memo,
            _jsonb(tags or []),
            signal_score_at_time,
            signal_value_at_time,
            source_agreement_at_time,
        )

    async def list_journals(
        self,
        *,
        user_id: int,
        stock_code: str | None = None,
        limit: int = 20,
    ) -> list[Any]:
        if stock_code:
            return await self._connection.fetch(
                """
                SELECT
                    signal_journals.*,
                    stocks.ticker,
                    stocks.name,
                    stocks.market,
                    stocks.sector
                FROM signal_journals
                INNER JOIN api.stocks
                    ON stocks.id = signal_journals.stock_id
                WHERE signal_journals.user_id = $1
                  AND stocks.ticker = $2
                ORDER BY signal_journals.created_at DESC
                LIMIT $3
                """,
                user_id,
                stock_code,
                limit,
            )
        return await self._connection.fetch(
            """
            SELECT
                signal_journals.*,
                stocks.ticker,
                stocks.name,
                stocks.market,
                stocks.sector
            FROM signal_journals
            INNER JOIN api.stocks
                ON stocks.id = signal_journals.stock_id
            WHERE signal_journals.user_id = $1
            ORDER BY signal_journals.created_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )

    async def get_journal(self, *, user_id: int, journal_id: int) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT
                signal_journals.*,
                stocks.ticker,
                stocks.name,
                stocks.market,
                stocks.sector
            FROM signal_journals
            INNER JOIN api.stocks
                ON stocks.id = signal_journals.stock_id
            WHERE signal_journals.id = $1
              AND signal_journals.user_id = $2
            """,
            journal_id,
            user_id,
        )

    async def update_journal(
        self,
        *,
        user_id: int,
        journal_id: int,
        user_view: str,
        user_memo: str | None,
        tags: list[str],
    ) -> Any:
        return await self._connection.fetchrow(
            """
            UPDATE signal_journals
            SET
                user_view = $3,
                user_memo = $4,
                tags = $5::JSONB,
                updated_at = NOW()
            FROM api.stocks
            WHERE signal_journals.id = $1
              AND signal_journals.user_id = $2
              AND stocks.id = signal_journals.stock_id
            RETURNING
                signal_journals.*,
                stocks.ticker,
                stocks.name,
                stocks.market,
                stocks.sector
            """,
            journal_id,
            user_id,
            user_view,
            user_memo,
            _jsonb(tags),
        )

    async def delete_journal(self, *, user_id: int, journal_id: int) -> None:
        await self._connection.execute(
            """
            DELETE FROM signal_journals
            WHERE id = $1
              AND user_id = $2
            """,
            journal_id,
            user_id,
        )

    async def list_latest_journals_by_stock_ids(self, *, user_id: int, stock_ids: list[int]) -> list[Any]:
        if not stock_ids:
            return []
        return await self._connection.fetch(
            """
            SELECT DISTINCT ON (stock_id)
                id,
                user_id,
                final_signal_id,
                stock_id,
                user_view,
                created_at
            FROM signal_journals
            WHERE user_id = $1
              AND stock_id = ANY($2::BIGINT[])
            ORDER BY stock_id, created_at DESC
            """,
            user_id,
            stock_ids,
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


def _jsonb(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
