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
                stocks.sector,
                '[]'::JSONB AS outcomes
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
                f"""
                SELECT
                    signal_journals.*,
                    stocks.ticker,
                    stocks.name,
                    stocks.market,
                    stocks.sector,
                    outcomes.items AS outcomes
                FROM signal_journals
                INNER JOIN api.stocks
                    ON stocks.id = signal_journals.stock_id
                {_outcomes_lateral("signal_journals")}
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
            f"""
            SELECT
                signal_journals.*,
                stocks.ticker,
                stocks.name,
                stocks.market,
                stocks.sector,
                outcomes.items AS outcomes
            FROM signal_journals
            INNER JOIN api.stocks
                ON stocks.id = signal_journals.stock_id
            {_outcomes_lateral("signal_journals")}
            WHERE signal_journals.user_id = $1
            ORDER BY signal_journals.created_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )

    async def get_journal(self, *, user_id: int, journal_id: int) -> Any:
        return await self._connection.fetchrow(
            f"""
            SELECT
                signal_journals.*,
                stocks.ticker,
                stocks.name,
                stocks.market,
                stocks.sector,
                outcomes.items AS outcomes
            FROM signal_journals
            INNER JOIN api.stocks
                ON stocks.id = signal_journals.stock_id
            {_outcomes_lateral("signal_journals")}
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
        # UPDATE ... FROM 의 LATERAL 은 갱신 대상 테이블을 참조할 수 없어 CTE 로 감싼다.
        return await self._connection.fetchrow(
            f"""
            WITH updated AS (
                UPDATE signal_journals
                SET
                    user_view = $3,
                    user_memo = $4,
                    tags = $5::JSONB,
                    updated_at = NOW()
                WHERE signal_journals.id = $1
                  AND signal_journals.user_id = $2
                RETURNING *
            )
            SELECT
                updated.*,
                stocks.ticker,
                stocks.name,
                stocks.market,
                stocks.sector,
                outcomes.items AS outcomes
            FROM updated
            INNER JOIN api.stocks
                ON stocks.id = updated.stock_id
            {_outcomes_lateral("updated")}
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

def _jsonb(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _outcomes_lateral(ref: str) -> str:
    """저널 행에 outcome 확정 결과(signal_journal_outcomes)를 JSONB 배열로 붙이는 LATERAL.

    horizon 정렬은 거래일 수 오름차순(7td → 30td)이 되도록 길이 우선 정렬을 쓴다.
    """
    return f"""LEFT JOIN LATERAL (
                SELECT COALESCE(
                    JSONB_AGG(
                        JSONB_BUILD_OBJECT(
                            'horizon', o.horizon,
                            'base_trade_date', o.base_trade_date,
                            'base_price', o.base_price,
                            'outcome_trade_date', o.outcome_trade_date,
                            'outcome_price', o.outcome_price,
                            'change_pct', o.change_pct,
                            'checked_at', o.checked_at
                        )
                        ORDER BY LENGTH(o.horizon), o.horizon
                    ),
                    '[]'::JSONB
                ) AS items
                FROM signal_journal_outcomes o
                WHERE o.journal_id = {ref}.id
            ) outcomes ON TRUE"""
