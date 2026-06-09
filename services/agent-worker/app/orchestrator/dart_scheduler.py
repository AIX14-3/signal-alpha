from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol

from app.orchestrator.task_types import COLLECT_DART


class StockRepository(Protocol):
    async def list_active(self, limit: int = 100) -> list[Any]:
        pass


class QueueRepository(Protocol):
    async def enqueue(self, **kwargs: Any) -> int:
        pass


class DartCollectionScheduler:
    def __init__(
        self,
        *,
        stock_repository: StockRepository,
        queue_repository: QueueRepository,
    ) -> None:
        self._stock_repository = stock_repository
        self._queue_repository = queue_repository

    async def enqueue_due_collections(
        self,
        *,
        limit: int = 100,
        end_de: str | None = None,
        priority: str = "batch",
    ) -> dict[str, Any]:
        resolved_end_de = _resolve_end_de(end_de)
        stocks = await self._stock_repository.list_active(limit=limit)

        task_ids = []
        for stock in stocks:
            task_id = await self._queue_repository.enqueue(
                stock_id=int(stock["id"]),
                task_type=COLLECT_DART,
                priority=priority,
                task_context={
                    "stock_code": str(stock["ticker"]).strip(),
                    "end_de": resolved_end_de,
                },
                dedupe=True,
            )
            task_ids.append(task_id)

        return {
            "scheduled_count": len(task_ids),
            "task_ids": task_ids,
        }


def _resolve_end_de(value: str | None) -> str:
    if not value:
        return date.today().strftime("%Y%m%d")
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return text
    return datetime.fromisoformat(text[:10]).strftime("%Y%m%d")
