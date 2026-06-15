from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.database import get_database_pool
from app.orchestrator.dart.scheduler import DartCollectionScheduler
from app.orchestrator.report.scheduler import ReportCollectionScheduler

router = APIRouter(prefix="/internal/schedules", tags=["schedules"])


class ScheduleDartCollectionRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000)
    end_de: str | None = None
    priority: Literal["batch", "immediate"] = "batch"


class ScheduleReportCollectionRequest(BaseModel):
    days_back: int = Field(default=7, ge=1, le=90)
    priority: Literal["batch", "immediate"] = "batch"


@router.post("/report/collect")
async def schedule_report_collection(
    request: ScheduleReportCollectionRequest,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import ProcessingQueueRepository, StockRepository

    async with pool.acquire() as connection:
        scheduler = ReportCollectionScheduler(
            stock_repository=StockRepository(connection),
            queue_repository=ProcessingQueueRepository(connection),
        )
        return await scheduler.enqueue_due_collections(
            days_back=request.days_back,
            priority=request.priority,
        )


@router.post("/dart/collect")
async def schedule_dart_collection(
    request: ScheduleDartCollectionRequest,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import ProcessingQueueRepository, StockRepository

    async with pool.acquire() as connection:
        scheduler = DartCollectionScheduler(
            stock_repository=StockRepository(connection),
            queue_repository=ProcessingQueueRepository(connection),
        )
        return await scheduler.enqueue_due_collections(
            limit=request.limit,
            end_de=request.end_de,
            priority=request.priority,
        )
