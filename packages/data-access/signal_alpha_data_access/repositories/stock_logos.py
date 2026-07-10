from __future__ import annotations

from typing import Any


class StockLogoRepository:
    """종목 회사 로고(public.stock_logo_published) 접근.

    backend 는 읽기(get_by_stock_id) 전용 — 공개 로고 API 가 PNG 바이트를 서빙한다.
    worker 발행 러너는 upsert 로 수집 DB(stock_logos)에서 읽은 로고를 backend DB 로
    동기화한다(stock_price_daily 와 같은 워커→백엔드 계약).
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def get_by_stock_id(self, *, stock_id: int) -> Any | None:
        """종목 로고 1행(image, mime_type) 또는 미보유 시 None(backend 읽기)."""
        return await self._connection.fetchrow(
            """
            SELECT image, mime_type
            FROM stock_logo_published
            WHERE stock_id = $1
            """,
            stock_id,
        )

    async def upsert(
        self,
        *,
        stock_id: int,
        image: bytes,
        mime_type: str,
        source: str | None,
    ) -> None:
        """종목 1행 멱등 upsert(worker 발행 러너 전용)."""
        await self._connection.execute(
            """
            INSERT INTO stock_logo_published (stock_id, image, mime_type, source)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (stock_id)
            DO UPDATE SET
                image = EXCLUDED.image,
                mime_type = EXCLUDED.mime_type,
                source = EXCLUDED.source,
                updated_at = now()
            """,
            stock_id,
            image,
            mime_type,
            source,
        )
