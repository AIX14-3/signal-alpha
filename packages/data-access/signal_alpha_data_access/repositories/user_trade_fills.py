"""유저 체결내역(trade fills) 리포지토리 (backend DB).

워커 동기화 러너가 브로커에서 조회한 체결을 (user_id, broker, broker_fill_id) 자연키로
멱등 적재(INSERT ... ON CONFLICT DO NOTHING — 체결은 불변). 증분 커서는 브로커별 마지막
filled_at. 부검(단건/패턴)은 backend 가 list_fills 로 읽는다. ticker→stock_id 매핑은
stocks(PUBLISHED, backend DB 공존)로 조회하며 실패 시 NULL(체결은 유지).

asyncpg 스타일: 키워드 전용 인자, $n 위치 파라미터.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

_FILL_COLUMNS = (
    "id, broker, account_ref, broker_fill_id, stock_id, ticker, side, "
    "filled_at, quantity, price, fee, created_at"
)


class UserTradeFillsRepository:
    """체결 적재(워커) + 증분 커서 + 부검 조회(backend). backend 연결 위에서 동작."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_fill(
        self,
        *,
        user_id: int,
        broker: str,
        account_ref: str,
        broker_fill_id: str,
        stock_id: int | None,
        ticker: str,
        side: str,
        filled_at: datetime,
        quantity: Any,
        price: Any,
        fee: Any = None,
    ) -> bool:
        """멱등 적재 — 이미 있으면 무시(체결 불변). 새로 넣었으면 True."""
        result = await self._connection.execute(
            """
            INSERT INTO user_trade_fills
                (user_id, broker, account_ref, broker_fill_id, stock_id, ticker,
                 side, filled_at, quantity, price, fee)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (user_id, broker, broker_fill_id) DO NOTHING
            """,
            user_id,
            broker,
            account_ref,
            broker_fill_id,
            stock_id,
            ticker,
            side,
            filled_at,
            quantity,
            price,
            fee,
        )
        return isinstance(result, str) and result.rsplit(" ", 1)[-1] == "1"

    async def last_filled_at(self, *, user_id: int, broker: str) -> datetime | None:
        """브로커별 마지막 체결 시각(증분 조회 커서). 없으면 None(최초 동기화)."""
        return await self._connection.fetchval(
            "SELECT max(filled_at) FROM user_trade_fills WHERE user_id = $1 AND broker = $2",
            user_id,
            broker,
        )

    async def resolve_stock_id(self, *, ticker: str) -> int | None:
        """종목코드 → stock_id. 비상장/해외 등 미매핑이면 None."""
        return await self._connection.fetchval(
            "SELECT id FROM stocks WHERE ticker = $1", ticker
        )

    async def list_fills(
        self, *, user_id: int, stock_id: int | None = None, limit: int = 500
    ) -> list[Any]:
        """부검용 체결 조회. stock_id 지정 시 단건(종목) 부검, 미지정 시 패턴 부검용 전체."""
        if stock_id is not None:
            return await self._connection.fetch(
                f"SELECT {_FILL_COLUMNS} FROM user_trade_fills "
                "WHERE user_id = $1 AND stock_id = $2 ORDER BY filled_at LIMIT $3",
                user_id,
                stock_id,
                limit,
            )
        return await self._connection.fetch(
            f"SELECT {_FILL_COLUMNS} FROM user_trade_fills "
            "WHERE user_id = $1 ORDER BY filled_at LIMIT $2",
            user_id,
            limit,
        )

    async def delete_for_user(self, *, user_id: int, broker: str | None = None) -> int:
        """유저 체결 데이터 삭제(연동해제 후 명시 정리). broker 지정 시 해당 브로커만."""
        if broker is not None:
            result = await self._connection.execute(
                "DELETE FROM user_trade_fills WHERE user_id = $1 AND broker = $2",
                user_id,
                broker,
            )
        else:
            result = await self._connection.execute(
                "DELETE FROM user_trade_fills WHERE user_id = $1", user_id
            )
        if isinstance(result, str):
            tail = result.rsplit(" ", 1)[-1]
            return int(tail) if tail.isdigit() else 0
        return 0
