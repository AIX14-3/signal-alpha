from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

from app.orchestrator.queue.task_types import COLLECT_REPORT


class StockRepository(Protocol):
    async def list_active(self, limit: int = 100) -> list[Any]:
        pass


class QueueRepository(Protocol):
    async def enqueue(self, **kwargs: Any) -> int:
        pass


class ReportCollectionScheduler:
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
        days_back: int = 7,
        priority: str = "batch",
    ) -> dict[str, Any]:
        stocks = await self._stock_repository.list_active(limit=limit)
        task_ids = []
        for stock in stocks:
            task_id = await self._queue_repository.enqueue(
                stock_id=int(stock["id"]),
                task_type=COLLECT_REPORT,
                priority=priority,
                task_context={
                    "stock_code": str(stock["ticker"]).strip(),
                    "days_back": days_back,
                },
                dedupe=True,
            )
            task_ids.append(task_id)
        return {"scheduled_count": len(task_ids), "task_ids": task_ids}
