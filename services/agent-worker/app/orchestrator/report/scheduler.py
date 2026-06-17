from __future__ import annotations

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
        date_start: str | None = None,
        date_end: str | None = None,
        max_pages: int = 20,
        priority: str = "batch",
    ) -> dict[str, Any]:
        stocks = await self._stock_repository.list_active(limit=limit)
        task_ids = []
        for stock in stocks:
            # 절대 날짜는 주어진 경우에만 task_context에 실어 보낸다.
            # 핸들러는 date_start/date_end가 있으면 그걸 우선, 없으면 days_back으로 fallback.
            task_context: dict[str, Any] = {
                "stock_code": str(stock["ticker"]).strip(),
                "days_back": days_back,
                "max_pages": max_pages,
            }
            if date_start is not None:
                task_context["date_start"] = date_start
            if date_end is not None:
                task_context["date_end"] = date_end

            task_id = await self._queue_repository.enqueue(
                stock_id=int(stock["id"]),
                task_type=COLLECT_REPORT,
                priority=priority,
                task_context=task_context,
                dedupe=True,
            )
            task_ids.append(task_id)
        return {"scheduled_count": len(task_ids), "task_ids": task_ids}
