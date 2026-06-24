from __future__ import annotations

from typing import Any


class ReportIssuanceRepository:
    """리포트 열람(언락) 쿼터 기록. 차감 단위 = final_signal 버전.

    멱등: (user_id, final_signal_id) UNIQUE → 동일 버전 재열람은 무차감.
    무료 잔여 = quota - COUNT(issued_via='free').
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def get_issuance(self, *, user_id: int, final_signal_id: int) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM report_issuances
            WHERE user_id = $1 AND final_signal_id = $2
            """,
            user_id,
            final_signal_id,
        )

    async def count_free_used(self, *, user_id: int) -> int:
        value = await self._connection.fetchval(
            """
            SELECT COUNT(*)
            FROM report_issuances
            WHERE user_id = $1 AND issued_via = 'free'
            """,
            user_id,
        )
        return int(value or 0)

    async def insert_issuance(
        self,
        *,
        user_id: int,
        stock_id: int,
        final_signal_id: int,
        run_key: str,
        issued_via: str,
    ) -> Any:
        """발행 기록 INSERT. 이미 존재(동일 버전)하면 ON CONFLICT 로 None 반환(무차감)."""
        return await self._connection.fetchrow(
            """
            INSERT INTO report_issuances (
                user_id, stock_id, final_signal_id, run_key, issued_via
            )
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id, final_signal_id) DO NOTHING
            RETURNING *
            """,
            user_id,
            stock_id,
            final_signal_id,
            run_key,
            issued_via,
        )
