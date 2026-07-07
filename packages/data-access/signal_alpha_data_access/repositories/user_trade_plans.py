"""유저 매매 계획 리포지토리 (backend DB).

매수 시점 계획(thesis·목표가·손절가·매도조건, 선택 입력). Plan vs Actual 부검의 기준선.
종목당 1개(user_id, ticker) upsert — 재진입 시 최신 계획으로 갱신.

asyncpg 스타일: 키워드 전용 인자, $n 위치 파라미터.
"""

from __future__ import annotations

from typing import Any

_PLAN_COLUMNS = (
    "id, stock_id, ticker, thesis, target_price, stop_price, sell_condition, "
    "planned_at, created_at, updated_at"
)


class UserTradePlanRepository:
    """매매 계획 CRUD. backend 연결 위에서 동작."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_plan(
        self,
        *,
        user_id: int,
        stock_id: int | None,
        ticker: str,
        thesis: str,
        target_price: Any = None,
        stop_price: Any = None,
        sell_condition: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            f"""
            INSERT INTO user_trade_plans
                (user_id, stock_id, ticker, thesis, target_price, stop_price, sell_condition)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (user_id, ticker) DO UPDATE SET
                stock_id = EXCLUDED.stock_id,
                thesis = EXCLUDED.thesis,
                target_price = EXCLUDED.target_price,
                stop_price = EXCLUDED.stop_price,
                sell_condition = EXCLUDED.sell_condition,
                updated_at = now()
            RETURNING {_PLAN_COLUMNS}
            """,
            user_id,
            stock_id,
            ticker,
            thesis,
            target_price,
            stop_price,
            sell_condition,
        )

    async def get_plan(self, *, user_id: int, ticker: str) -> Any:
        return await self._connection.fetchrow(
            f"SELECT {_PLAN_COLUMNS} FROM user_trade_plans WHERE user_id = $1 AND ticker = $2",
            user_id,
            ticker,
        )

    async def list_plans(self, *, user_id: int) -> list[Any]:
        return await self._connection.fetch(
            f"SELECT {_PLAN_COLUMNS} FROM user_trade_plans WHERE user_id = $1 ORDER BY ticker",
            user_id,
        )

    async def delete_plan(self, *, user_id: int, ticker: str) -> bool:
        result = await self._connection.execute(
            "DELETE FROM user_trade_plans WHERE user_id = $1 AND ticker = $2",
            user_id,
            ticker,
        )
        return isinstance(result, str) and result.rsplit(" ", 1)[-1] == "1"
