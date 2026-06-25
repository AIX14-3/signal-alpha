from __future__ import annotations

from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Depends

from app.api.routes.auth import NOTICE
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import ProcessingQueueRepository


analytics_router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# processing_queue.status → 스테퍼 표시 상태
_ACTIVE = ("running", "retrying")
_DONE = ("success", "skipped")


@analytics_router.get("/{ticker}/status")
async def get_analysis_status(
    ticker: str,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """종목 분석 파이프라인 진행 상태(폴링). processing_queue task를 단계로 집계한다."""
    async with pool.acquire() as connection:
        rows = await ProcessingQueueRepository(connection).list_tasks(
            stock_code=ticker, limit=100
        )
    tasks = [dict(row) for row in rows]
    stages = _latest_per_task_type(tasks)
    return {
        "ticker": ticker,
        "overall": _overall_status([stage["status"] for stage in stages]),
        "stages": stages,
        "notice": NOTICE,
    }


def _latest_per_task_type(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # list_tasks는 created_at DESC 정렬 → task_type별 첫 등장이 최신.
    latest: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for task in tasks:
        task_type = task.get("task_type")
        if task_type in latest:
            continue
        latest[task_type] = {
            "task_type": task_type,
            "status": task.get("status"),
            "updated_at": _timestamp(task.get("updated_at") or task.get("created_at")),
        }
    return list(latest.values())


def _overall_status(statuses: list[str]) -> str:
    if not statuses:
        return "pending"
    if any(status in _ACTIVE for status in statuses):
        return "running"
    if any(status == "pending" for status in statuses):
        return "pending"
    if any(status == "failed" for status in statuses):
        return "failed"
    if all(status in _DONE for status in statuses):
        return "success"
    return "running"


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
