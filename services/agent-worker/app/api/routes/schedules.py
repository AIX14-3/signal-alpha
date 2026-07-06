from __future__ import annotations

from collections import defaultdict
from datetime import date
from datetime import datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.database import get_database_pool
from app.orchestrator.dart.scheduler import DartCollectionScheduler
from app.orchestrator.report.scheduler import ReportCollectionScheduler

router = APIRouter(prefix="/internal/schedules", tags=["schedules"])


class ScheduleDartCollectionRequest(BaseModel):
    # DART 수집은 종목당 무겁다(공시리스트+문서 fetch). 한 회차 인큐를 작게 잡아 워커 독점을
    # 막는다(공정 drain=run-cycle 와 함께). 더 자주 실행해 전 종목을 점진 수집한다.
    limit: int = Field(default=10, ge=1, le=1000)
    end_de: str | None = None
    priority: Literal["batch", "immediate"] = "batch"
    include_ownership: bool = False


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


class SchedulerDryRunRequest(BaseModel):
    schedule: dict[str, Any]
    now: datetime | None = None
    queue_stats: dict[str, Any] | None = None


def _parse_time(value: Any) -> time | None:
    if value is None or isinstance(value, time):
        return value
    if isinstance(value, str):
        return time.fromisoformat(value)
    return value


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _normalize_schedule_payload(schedule: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(schedule)
    for key in ("run_at_local", "active_from_local", "active_until_local"):
        normalized[key] = _parse_time(normalized.get(key))
    for key in ("last_run_at", "manual_trigger_requested_at", "next_run_at", "updated_at"):
        normalized[key] = _parse_datetime(normalized.get(key))
    return normalized


async def _queue_stats(pool: Any) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import ObservabilityRepository

    async with pool.acquire() as connection:
        observability = ObservabilityRepository(connection)
        rows = await observability.queue_stats()
        # 스케줄러 backpressure 와 동일 기준(failed 총계는 평생 누적 → 최근 윈도우 카운트).
        failed_recent = await observability.recent_failed_count(window_minutes=360)

    items = [dict(row) for row in rows]
    totals_by_status: dict[str, int] = defaultdict(int)
    for item in items:
        totals_by_status[item["status"]] += int(item["count"])
    return {
        "total": sum(totals_by_status.values()),
        "totals_by_status": dict(totals_by_status),
        "items": items,
        "failed_recent": failed_recent,
        "failed_window_minutes": 360,
    }


@router.post("/dry-run")
async def schedule_dry_run(
    request: SchedulerDryRunRequest,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from run_scheduler_instance import _scheduler_dry_run

    schedule = _normalize_schedule_payload(request.schedule)
    # 알 수 없는 timezone 은 ZoneInfo 가 dry-run 내부에서 터져 500 이 되므로 여기서 422 로 거른다.
    timezone_name = str(schedule.get("timezone") or "Asia/Seoul")
    try:
        tz = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"unknown timezone: {timezone_name}",
        ) from exc
    now = request.now
    if now is not None and now.tzinfo is None:
        # naive now 는 스케줄 타임존 기준으로 해석 — 그대로 두면 aware last_run_at 등과의
        # 비교(TypeError)로 500 이 나거나 시스템 로컬 타임존으로 잘못 평가된다.
        now = now.replace(tzinfo=tz)

    queue_stats = request.queue_stats if request.queue_stats is not None else await _queue_stats(pool)
    return _scheduler_dry_run(
        schedule,
        now=now,
        queue_stats=queue_stats,
    )


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
    from app.orchestrator.report.normalize_backfill import (
        schedule_report_normalize_backfill as schedule_backfill,
    )

    async with pool.acquire() as connection:
        return await schedule_backfill(
            connection,
            stock_code=request.stock_code,
            limit=request.limit,
            priority=request.priority,
            dry_run=request.dry_run,
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
            include_ownership=request.include_ownership,
        )
