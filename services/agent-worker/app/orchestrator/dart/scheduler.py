from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol

from app.orchestrator.queue.task_types import (
    COLLECT_DART,
    COLLECT_DART_EMPLOYEE,
    COLLECT_DART_FINANCIALS,
    COLLECT_DART_OWNERSHIP,
)


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
        limit: int = 10,
        end_de: str | None = None,
        priority: str = "batch",
        include_ownership: bool = False,
        include_financials: bool = False,
        include_employee: bool = False,
    ) -> dict[str, Any]:
        resolved_end_de = _resolve_end_de(end_de)
        stocks = await self._stock_repository.list_active(limit=limit)

        task_ids = []
        collect_dart_task_ids = []
        collect_dart_ownership_task_ids = []
        collect_dart_financials_task_ids = []
        collect_dart_employee_task_ids = []
        for stock in stocks:
            stock_id = int(stock["id"])
            stock_code = str(stock["ticker"]).strip()
            task_id = await self._queue_repository.enqueue(
                stock_id=stock_id,
                task_type=COLLECT_DART,
                priority=priority,
                task_context={
                    "stock_code": stock_code,
                    "end_de": resolved_end_de,
                },
                dedupe=True,
            )
            task_ids.append(task_id)
            collect_dart_task_ids.append(task_id)
            if include_ownership:
                ownership_task_id = await self._queue_repository.enqueue(
                    stock_id=stock_id,
                    task_type=COLLECT_DART_OWNERSHIP,
                    priority=priority,
                    task_context={"stock_code": stock_code},
                    dedupe=True,
                )
                task_ids.append(ownership_task_id)
                collect_dart_ownership_task_ids.append(ownership_task_id)
            if include_financials:
                financials_task_id = await self._queue_repository.enqueue(
                    stock_id=stock_id,
                    task_type=COLLECT_DART_FINANCIALS,
                    priority=priority,
                    task_context={"stock_code": stock_code},
                    dedupe=True,
                )
                task_ids.append(financials_task_id)
                collect_dart_financials_task_ids.append(financials_task_id)
            if include_employee:
                employee_task_id = await self._queue_repository.enqueue(
                    stock_id=stock_id,
                    task_type=COLLECT_DART_EMPLOYEE,
                    priority=priority,
                    task_context={"stock_code": stock_code},
                    dedupe=True,
                )
                task_ids.append(employee_task_id)
                collect_dart_employee_task_ids.append(employee_task_id)

        result = {
            "scheduled_count": len(task_ids),
            "task_ids": task_ids,
        }
        if include_ownership or include_financials or include_employee:
            result["collect_dart_task_ids"] = collect_dart_task_ids
        if include_ownership:
            result["collect_dart_ownership_task_ids"] = collect_dart_ownership_task_ids
        if include_financials:
            result["collect_dart_financials_task_ids"] = collect_dart_financials_task_ids
        if include_employee:
            result["collect_dart_employee_task_ids"] = collect_dart_employee_task_ids
        return result


def _resolve_end_de(value: str | None) -> str:
    if not value:
        return date.today().strftime("%Y%m%d")
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return text
    return datetime.fromisoformat(text[:10]).strftime("%Y%m%d")
