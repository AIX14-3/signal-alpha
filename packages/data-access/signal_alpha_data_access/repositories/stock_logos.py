from __future__ import annotations

from typing import Any


class StockLogoRepository:
    """종목 회사 로고(public.stock_logo_published) 접근.

    backend DB 가 로고 원본을 직접 보유한다(수집 DB 복사·발행 없음). 정적 참조 데이터라
    적재 툴(database/tools/load_stock_logos.py)이 backend DB 로 1회성 upsert 하고,
    공개 로고 API 는 여기서 읽기(get_by_stock_id)만 한다. 테이블 이름은 과거 발행 사본
    마이그(20260710_1100)의 이름을 그대로 유지한다(db_partition check_targets 의 과거
    마이그 locality 재검증과 충돌하지 않게 이름을 바꾸지 않는다).
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
