from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.core.database import get_database_pool
from app.orchestrator.dart.scheduler import DartCollectionScheduler
from app.orchestrator.report.scheduler import ReportCollectionScheduler

router = APIRouter(prefix="/internal/schedules", tags=["schedules"])


class ScheduleDartCollectionRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000)
    end_de: str | None = None
    priority: Literal["batch", "immediate"] = "batch"


class ScheduleReportCollectionRequest(BaseModel):
    # 절대 날짜 범위 (예: "2025-01-01"). 둘 다 주면 days_back보다 우선한다.
    date_start: str | None = None
    date_end: str | None = None
    days_back: int = Field(default=7, ge=1, le=400)
    # 12개월치 같은 넓은 범위는 기본 20p로 부족할 수 있어 상향 가능하게 노출.
    # 크롤러는 date_start 이전 리포트 도달 시 조기 중단하므로 과수집 위험은 없음.
    max_pages: int = Field(default=20, ge=1, le=200)
    limit: int = Field(default=100, ge=1, le=1000)
    priority: Literal["batch", "immediate"] = "batch"

    @field_validator("date_start", "date_end")
    @classmethod
    def _validate_iso_date(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("YYYY-MM-DD 형식이어야 합니다") from exc
        return value


class ScheduleReportAnalysisRequest(BaseModel):
    # 특정 종목만 분석. 없으면 active 종목 전체.
    stock_code: str | None = None
    analysis_date: str | None = None
    run_key: str = "REPORT"
    limit: int = Field(default=100, ge=1, le=1000)
    priority: Literal["batch", "immediate"] = "batch"

    @field_validator("analysis_date")
    @classmethod
    def _validate_iso_date(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("YYYY-MM-DD 형식이어야 합니다") from exc
        return value


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
            limit=request.limit,
            days_back=request.days_back,
            date_start=request.date_start,
            date_end=request.date_end,
            max_pages=request.max_pages,
            priority=request.priority,
        )


@router.post("/report/analyze")
async def schedule_report_analysis(
    request: ScheduleReportAnalysisRequest,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import ProcessingQueueRepository, StockRepository

    from app.orchestrator.queue.task_types import ANALYZE_REPORT

    async with pool.acquire() as connection:
        stock_repository = StockRepository(connection)
        queue_repository = ProcessingQueueRepository(connection)

        if request.stock_code:
            stock = await stock_repository.get_by_ticker(request.stock_code)
            stocks = [stock] if stock else []
        else:
            stocks = await stock_repository.list_active(limit=request.limit)

        task_context_base: dict[str, Any] = {"run_key": request.run_key}
        if request.analysis_date:
            task_context_base["analysis_date"] = request.analysis_date

        task_ids: list[int] = []
        for stock in stocks:
            task_id = await queue_repository.enqueue(
                stock_id=int(stock["id"]),
                task_type=ANALYZE_REPORT,
                priority=request.priority,
                task_context={
                    **task_context_base,
                    "stock_code": str(stock["ticker"]).strip(),
                },
                dedupe=True,
            )
            task_ids.append(task_id)

        return {"scheduled_count": len(task_ids), "task_ids": task_ids}


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
