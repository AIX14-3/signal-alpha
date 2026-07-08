from __future__ import annotations

from typing import Any


class StockPriceRepository:
    """종목별 일봉 종가 시리즈(public.stock_price_daily) 접근.

    backend 는 읽기(list_daily_close) 전용 — 공개 홈 차트 API 가 종가 라인을 그린다.
    worker 발행 러너는 upsert_daily_close 로 수집 DB(ohlcv_data)에서 읽은 종가를
    backend DB 로 동기화한다(signal_journal_chart_prices 와 같은 워커→백엔드 계약).
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def list_daily_close(self, *, stock_id: int, from_date: Any) -> list[Any]:
        """from_date 이상의 (trade_date, close_price) 오름차순 시리즈(backend 읽기)."""
        return await self._connection.fetch(
            """
            SELECT trade_date, close_price
            FROM stock_price_daily
            WHERE stock_id = $1
              AND trade_date >= $2
            ORDER BY trade_date ASC
            """,
            stock_id,
            from_date,
        )

    async def upsert_daily_close(
        self,
        *,
        stock_id: int,
        trade_date: Any,
        close_price: Any,
    ) -> None:
        """종목×거래일 1행 멱등 upsert(worker 발행 러너 전용)."""
        await self._connection.execute(
            """
            INSERT INTO stock_price_daily (stock_id, trade_date, close_price)
            VALUES ($1, $2, $3)
            ON CONFLICT (stock_id, trade_date)
            DO UPDATE SET
                close_price = EXCLUDED.close_price,
                updated_at = now()
            """,
            stock_id,
            trade_date,
            close_price,
        )
