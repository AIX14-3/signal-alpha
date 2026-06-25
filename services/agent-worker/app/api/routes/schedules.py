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


class ScheduleReportNormalizeBackfillRequest(BaseModel):
    stock_code: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    priority: Literal["batch", "immediate"] = "batch"
    dry_run: bool = True


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


@router.post("/report/normalize-backfill")
async def schedule_report_normalize_backfill(
    request: ScheduleReportNormalizeBackfillRequest,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import (
        ProcessingQueueRepository,
        RawDetailRepository,
        StockRepository,
    )

    from app.orchestrator.queue.task_types import NORMALIZE_REPORT

    async with pool.acquire() as connection:
        stock_id: int | None = None
        if request.stock_code:
            stock = await StockRepository(connection).get_by_ticker(request.stock_code)
            if stock is None:
                return {
                    "dry_run": request.dry_run,
                    "candidate_count": 0,
                    "scheduled_count": 0,
                    "task_ids": [],
                    "candidates": [],
                }
            stock_id = int(stock["id"])

        candidates = [
            dict(row)
            for row in await RawDetailRepository(connection).list_report_normalize_backfill_candidates(
                stock_id=stock_id,
                limit=request.limit,
            )
        ]

        task_ids: list[int] = []
        if not request.dry_run:
            queue_repository = ProcessingQueueRepository(connection)
            for candidate in candidates:
                raw_document_id = int(candidate["raw_document_id"])
                stock_code = str(candidate.get("stock_code") or request.stock_code or "").strip()
                task_id = await queue_repository.enqueue(
                    stock_id=int(candidate["stock_id"]),
                    task_type=NORMALIZE_REPORT,
                    priority=request.priority,
                    source_raw_ids=[raw_document_id],
                    task_context={
                        "raw_document_id": raw_document_id,
                        "stock_code": stock_code,
                        "source_type": "REPORT",
                    },
                    dedupe=True,
                )
                task_ids.append(task_id)

        return {
            "dry_run": request.dry_run,
            "candidate_count": len(candidates),
            "scheduled_count": len(task_ids),
            "task_ids": task_ids,
            "candidates": candidates,
        }


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
