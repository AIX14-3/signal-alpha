"""PUBLISH_SIGNALS 큐 핸들러 — 종목 발행 산출물을 백엔드 DB로 발행 (#11).

AGGREGATE 가 final_signal 을 발행하면(+백엔드 DB 설정 시) 이 태스크가 인큐되어, 해당 종목의
PUBLISHED 테이블을 수집 DB → 백엔드 DB 로 복사한다(``signal_publisher.publish_stock``).

경계/게이트:
- ``BACKEND_DATABASE_URL`` 미설정이면 단일 DB 모드 → no-op(발행 생략). 결정론 폴백 없음.
- 멱등 — 재실행 시 백엔드 행을 최신으로 upsert. 한 종목 발행을 백엔드 트랜잭션으로 묶어
  부분 발행을 막는다.
- 백엔드 연결은 작업당 단발 생성(발행 빈도 낮음). 테스트는 ``backend_connector`` 주입.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Awaitable, Callable

from app.core.config import get_settings
from app.orchestrator.queue.context import parse_task_context
from app.publish.signal_publisher import publish_stock

BackendConnector = Callable[[str], Awaitable[Any]]


class PublishSignalsTaskHandler:
    def __init__(
        self,
        connection: Any,
        *,
        settings: Any = None,
        backend_connector: BackendConnector | None = None,
    ) -> None:
        self._connection = connection  # 수집(source) DB 연결
        self._settings = settings or get_settings()
        self._backend_connector = backend_connector or _default_connector

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        backend_url = getattr(self._settings, "backend_database_url", None)
        if not backend_url:
            # 단일 DB 모드 — 발행 대상 없음.
            return {"stock_id": stock_id, "skipped_reason": "no_backend_db"}

        parse_task_context(task.get("task_context"))  # 컨텍스트 정규화(향후 확장 여지)
        backend = await self._backend_connector(backend_url)
        try:
            async with backend.transaction():
                published = await publish_stock(self._connection, backend, stock_id=stock_id)
        finally:
            await backend.close()
        return {"stock_id": stock_id, "published": published}


async def _default_connector(url: str) -> Any:
    import asyncpg

    return await asyncpg.connect(url)
