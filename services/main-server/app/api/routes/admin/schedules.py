"""어드민 수집 스케줄 제어 (collection_schedules).

어드민이 백엔드 DB 의 단일 config 행을 읽고/쓰면, 워커측 스케줄러가 폴링해 발화한다
(main-server → worker 직접 호출 없음). 상태(last/next run)는 스케줄러가 기록한다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.routes.admin._serializers import (
    _audit_json,
    _parse_schedule_time,
    _schedule_row,
    _schedule_run_row,
    _validate_active_window,
    _validate_price_modes,
    _validate_range,
    _validate_targets,
)
from app.api.routes.admin._worker import _worker_request
from app.api.routes.admin_auth import admin_error, get_current_admin
from app.core.config import Settings, get_settings
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import (
    AdminRepository,
    CollectionScheduleRepository,
    parse_schedule_row,
)

router = APIRouter()


class UpdateScheduleRequest(BaseModel):
    """수집 스케줄 config 수정(보낸 필드만 변경)."""

    enabled: bool | None = None
    run_at_local: str | None = None  # "HH:MM"
    timezone: str | None = None
    targets: list[str] | None = None
    dart_limit: int | None = None
    price_modes: list[str] | None = None
    report_limit: int | None = None
    report_days_back: int | None = None
    report_max_pages: int | None = None
    alternative_collect_enabled: bool | None = None
    alternative_analyze_enabled: bool | None = None
    alternative_collect_timeout_seconds: int | None = None
    alternative_analyze_timeout_seconds: int | None = None
    backpressure_max_waiting: int | None = None
    backpressure_max_failed: int | None = None
    frequency_minutes: int | None = None
    active_from_local: str | None = None
    active_until_local: str | None = None


@router.get("/schedules")
async def list_schedules(
    _admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        rows = await CollectionScheduleRepository(connection).list_all()
    return {"items": [_schedule_row(parse_schedule_row(row)) for row in rows]}


@router.put("/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: int,
    payload: UpdateScheduleRequest,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    run_at = _parse_schedule_time(payload.run_at_local) if payload.run_at_local is not None else None
    active_from = (
        _parse_schedule_time(payload.active_from_local)
        if payload.active_from_local is not None
        else None
    )
    active_until = (
        _parse_schedule_time(payload.active_until_local)
        if payload.active_until_local is not None
        else None
    )
    targets = _validate_targets(payload.targets) if payload.targets is not None else None
    price_modes = (
        _validate_price_modes(payload.price_modes) if payload.price_modes is not None else None
    )
    if payload.dart_limit is not None and not (1 <= payload.dart_limit <= 1000):
        raise admin_error(400, "INVALID_DART_LIMIT", "dart_limit 은 1~1000 이어야 합니다.")
    _validate_range(payload.report_limit, "report_limit", 1, 1000)
    _validate_range(payload.report_days_back, "report_days_back", 1, 400)
    _validate_range(payload.report_max_pages, "report_max_pages", 1, 200)
    _validate_range(
        payload.alternative_collect_timeout_seconds,
        "alternative_collect_timeout_seconds",
        60,
        86400,
    )
    _validate_range(
        payload.alternative_analyze_timeout_seconds,
        "alternative_analyze_timeout_seconds",
        60,
        86400,
    )
    _validate_range(payload.backpressure_max_waiting, "backpressure_max_waiting", 0, 1_000_000)
    _validate_range(payload.backpressure_max_failed, "backpressure_max_failed", 0, 1_000_000)
    if payload.frequency_minutes is not None and not (1 <= payload.frequency_minutes <= 1440):
        raise admin_error(
            400,
            "INVALID_FREQUENCY_MINUTES",
            "frequency_minutes 는 1~1440 이어야 합니다.",
        )
    async with pool.acquire() as connection:
        repo = CollectionScheduleRepository(connection)
        before = await repo.get_by_id(schedule_id)
        if before is None:
            raise admin_error(404, "SCHEDULE_NOT_FOUND", "스케줄을 찾을 수 없습니다.")
        before_data = parse_schedule_row(before)
        _validate_active_window(
            frequency_minutes=(
                payload.frequency_minutes
                if payload.frequency_minutes is not None
                else before_data.get("frequency_minutes")
            ),
            active_from=(
                active_from
                if payload.active_from_local is not None
                else before_data.get("active_from_local")
            ),
            active_until=(
                active_until
                if payload.active_until_local is not None
                else before_data.get("active_until_local")
            ),
        )
        updated = await repo.update_config(
            schedule_id=schedule_id,
            enabled=payload.enabled,
            run_at_local=run_at,
            timezone=payload.timezone,
            targets=targets,
            dart_limit=payload.dart_limit,
            price_modes=price_modes,
            report_limit=payload.report_limit,
            report_days_back=payload.report_days_back,
            report_max_pages=payload.report_max_pages,
            alternative_collect_enabled=payload.alternative_collect_enabled,
            alternative_analyze_enabled=payload.alternative_analyze_enabled,
            alternative_collect_timeout_seconds=payload.alternative_collect_timeout_seconds,
            alternative_analyze_timeout_seconds=payload.alternative_analyze_timeout_seconds,
            backpressure_max_waiting=payload.backpressure_max_waiting,
            backpressure_max_failed=payload.backpressure_max_failed,
            frequency_minutes=payload.frequency_minutes,
            active_from_local=active_from,
            active_until_local=active_until,
            updated_by=str(admin.get("admin_email") or admin.get("admin_id")),
        )
        await AdminRepository(connection).record_audit_log(
            actor_admin_id=int(admin["admin_id"]),
            action="schedule.update",
            target_type="schedule",
            target_id=schedule_id,
            before=_audit_json(before_data),
            after=_audit_json(parse_schedule_row(updated)),
        )
    return _schedule_row(parse_schedule_row(updated))


@router.get("/schedules/{schedule_id}/runs")
async def list_schedule_runs(
    schedule_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    _admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        rows = await CollectionScheduleRepository(connection).list_recent_runs(
            schedule_id=schedule_id,
            limit=limit,
        )
    return {"items": [_schedule_run_row(dict(row)) for row in rows]}


@router.post("/schedules/{schedule_id}/dry-run")
async def dry_run_schedule(
    schedule_id: int,
    _admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        row = await CollectionScheduleRepository(connection).get_by_id(schedule_id)
    if row is None:
        raise admin_error(404, "SCHEDULE_NOT_FOUND", "스케줄을 찾을 수 없습니다.")
    return await _worker_request(
        "POST",
        "/internal/schedules/dry-run",
        settings=settings,
        json_body={"schedule": _schedule_row(parse_schedule_row(row))},
    )


@router.post("/schedules/{schedule_id}/trigger")
async def trigger_schedule(
    schedule_id: int,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """'지금 실행' — manual_trigger 표시. 워커 스케줄러가 다음 폴링에 발화한다."""
    async with pool.acquire() as connection:
        repo = CollectionScheduleRepository(connection)
        updated = await repo.request_manual_trigger(
            schedule_id=schedule_id,
            updated_by=str(admin.get("admin_email") or admin.get("admin_id")),
        )
        if updated is None:
            raise admin_error(404, "SCHEDULE_NOT_FOUND", "스케줄을 찾을 수 없습니다.")
        await AdminRepository(connection).record_audit_log(
            actor_admin_id=int(admin["admin_id"]),
            action="schedule.trigger",
            target_type="schedule",
            target_id=schedule_id,
        )
    return _schedule_row(parse_schedule_row(updated))
