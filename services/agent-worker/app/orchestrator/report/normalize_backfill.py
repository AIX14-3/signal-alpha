from __future__ import annotations

from typing import Any, Literal

from app.orchestrator.queue.task_types import NORMALIZE_REPORT
from signal_alpha_data_access.repositories import (
    ProcessingQueueRepository,
    RawDetailRepository,
    StockRepository,
)

Priority = Literal["batch", "immediate"]


async def schedule_report_normalize_backfill(
    connection: Any,
    *,
    stock_code: str | None = None,
    limit: int = 100,
    priority: Priority = "batch",
    dry_run: bool = True,
) -> dict[str, Any]:
    stock_id: int | None = None
    if stock_code:
        stock = await StockRepository(connection).get_by_ticker(stock_code)
        if stock is None:
            return _empty_summary(dry_run=dry_run)
        stock_id = int(stock["id"])

    candidates = [
        dict(row)
        for row in await RawDetailRepository(connection).list_report_normalize_backfill_candidates(
            stock_id=stock_id,
            limit=limit,
        )
    ]

    task_ids: list[int] = []
    enqueued_count = 0
    reused_count = 0
    if not dry_run:
        queue_repository = ProcessingQueueRepository(connection)
        for candidate in candidates:
            raw_document_id = int(candidate["raw_document_id"])
            candidate_stock_code = str(candidate.get("stock_code") or stock_code or "").strip()
            enqueue_result = await queue_repository.enqueue_with_status(
                stock_id=int(candidate["stock_id"]),
                task_type=NORMALIZE_REPORT,
                priority=priority,
                source_raw_ids=[raw_document_id],
                task_context={
                    "raw_document_id": raw_document_id,
                    "stock_code": candidate_stock_code,
                    "source_type": "REPORT",
                },
                dedupe=True,
            )
            task_ids.append(int(enqueue_result["task_id"]))
            if enqueue_result["reused"]:
                reused_count += 1
            else:
                enqueued_count += 1

    return {
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "scheduled_count": len(task_ids),
        "enqueued_count": enqueued_count,
        "reused_count": reused_count,
        "task_ids": task_ids,
        "candidates": candidates,
    }


def _empty_summary(*, dry_run: bool) -> dict[str, Any]:
    return {
        "dry_run": dry_run,
        "candidate_count": 0,
        "scheduled_count": 0,
        "enqueued_count": 0,
        "reused_count": 0,
        "task_ids": [],
        "candidates": [],
    }
