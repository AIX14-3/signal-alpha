"""유저 체결내역(trade fills) 리포지토리 (backend DB).

유저가 매수/매도 체결(일자·수량·가격)을 직접(수기) 입력한다. main-server(signal_backend)가
INSERT(입력)·DELETE(삭제)하고, 부검(단건/패턴)은 list_fills 로 읽는다. ticker→stock_id
매핑은 stocks(PUBLISHED, backend DB 공존)로 조회하며 실패 시 NULL(체결은 유지, overlay만 제한).

asyncpg 스타일: 키워드 전용 인자, $n 위치 파라미터.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

_FILL_COLUMNS = (
    "id, stock_id, ticker, side, filled_at, quantity, price, fee, created_at"
)


class UserTradeFillsRepository:
    """수기 체결 입력·삭제(backend) + 부검 조회. backend 연결 위에서 동작."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def insert_fill(
        self,
        *,
        user_id: int,
        stock_id: int | None,
        ticker: str,
        side: str,
        filled_at: datetime,
        quantity: Any,
        price: Any,
        fee: Any = None,
    ) -> Any:
        """수기 체결 1건 추가. 삽입한 행을 반환(id 포함)."""
        return await self._connection.fetchrow(
            f"""
            INSERT INTO user_trade_fills
                (user_id, stock_id, ticker, side, filled_at, quantity, price, fee)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING {_FILL_COLUMNS}
            """,
            user_id,
            stock_id,
            ticker,
            side,
            filled_at,
            quantity,
            price,
            fee,
        )

    async def list_fills(
        self, *, user_id: int, stock_id: int | None = None, limit: int = 500
    ) -> list[Any]:
        """부검용 체결 조회. stock_id 지정 시 단건(종목) 부검, 미지정 시 패턴 부검용 전체.

        수기 입력은 날짜만 받아 같은 날 체결이 동일 filled_at 이 되므로, id 로 2차 정렬해
        조회 순서를 결정적으로 만든다(라운드트립 페어링·표시 순서 안정).
        """
        if stock_id is not None:
            return await self._connection.fetch(
                f"SELECT {_FILL_COLUMNS} FROM user_trade_fills "
                "WHERE user_id = $1 AND stock_id = $2 ORDER BY filled_at, id LIMIT $3",
                user_id,
                stock_id,
                limit,
            )
        return await self._connection.fetch(
            f"SELECT {_FILL_COLUMNS} FROM user_trade_fills "
            "WHERE user_id = $1 ORDER BY filled_at, id LIMIT $2",
            user_id,
            limit,
        )

    async def delete_fill(self, *, user_id: int, fill_id: int) -> bool:
        """수기 입력한 체결 1건 삭제(오입력 정정). 삭제됐으면 True."""
        result = await self._connection.execute(
            "DELETE FROM user_trade_fills WHERE user_id = $1 AND id = $2",
            user_id,
            fill_id,
        )
        return isinstance(result, str) and result.rsplit(" ", 1)[-1] == "1"

    async def delete_for_user(self, *, user_id: int) -> int:
        """유저 체결 데이터 전체 삭제(계정 정리 등)."""
        result = await self._connection.execute(
            "DELETE FROM user_trade_fills WHERE user_id = $1", user_id
        )
        if isinstance(result, str):
            tail = result.rsplit(" ", 1)[-1]
            return int(tail) if tail.isdigit() else 0
        return 0
