from datetime import UTC, datetime, timedelta, time

import pytest

from app.api.routes.admin import (
    _schedule_health,
    _schedule_row,
    _validate_active_window,
    _validate_targets,
)


def test_validate_targets_allows_alternative_scheduler_target():
    assert _validate_targets([" alternative ", "price"]) == ["alternative", "price"]


def test_validate_targets_rejects_empty_scheduler_target_selection():
    with pytest.raises(Exception) as exc_info:
        _validate_targets([" ", ""])

    assert exc_info.value.detail["code"] == "EMPTY_TARGETS"


def test_validate_active_window_rejects_zero_length_interval_schedule():
    with pytest.raises(Exception) as exc_info:
        _validate_active_window(
            frequency_minutes=60,
            active_from=time(9, 0),
            active_until=time(9, 0),
        )

    assert exc_info.value.detail["code"] == "INVALID_ACTIVE_WINDOW"


def test_schedule_health_flags_delayed_schedule_after_grace_period():
    now = datetime(2026, 7, 1, 9, 20, tzinfo=UTC)
    health = _schedule_health(
        {
            "enabled": True,
            "last_status": "success",
            "next_run_at": now - timedelta(minutes=20),
        },
        now=now,
        grace_minutes=15,
    )

    assert health["status"] == "delayed"


def test_schedule_health_flags_recent_failed_schedule_before_next_due():
    now = datetime(2026, 7, 1, 9, 20, tzinfo=UTC)
    health = _schedule_health(
        {
            "enabled": True,
            "last_status": "failed",
            "next_run_at": now + timedelta(minutes=40),
        },
        now=now,
        grace_minutes=15,
    )

    assert health["status"] == "failed_waiting"


def test_schedule_row_serializes_cadence_fields():
    assert _schedule_row(
        {
            "id": 1,
            "name": "dart-collection",
            "enabled": True,
            "run_at_local": time(8, 30),
            "timezone": "Asia/Seoul",
            "targets": ["dart"],
            "dart_limit": 100,
            "price_modes": ["snapshot"],
            "report_limit": 50,
            "report_days_back": 5,
            "report_max_pages": 12,
            "alternative_collect_enabled": True,
            "alternative_analyze_enabled": False,
            "alternative_collect_timeout_seconds": 900,
            "alternative_analyze_timeout_seconds": 1200,
            "backpressure_max_waiting": 20,
            "backpressure_max_failed": 3,
            "frequency_minutes": 60,
            "active_from_local": time(8, 30),
            "active_until_local": time(20, 30),
            "next_run_at": datetime(2099, 1, 1, 0, 0, tzinfo=UTC),
        }
    ) == {
        "id": 1,
        "name": "dart-collection",
        "enabled": True,
        "run_at_local": "08:30",
        "timezone": "Asia/Seoul",
        "targets": ["dart"],
        "dart_limit": 100,
        "price_modes": ["snapshot"],
        "report_limit": 50,
        "report_days_back": 5,
        "report_max_pages": 12,
        "alternative_collect_enabled": True,
        "alternative_analyze_enabled": False,
        "alternative_collect_timeout_seconds": 900,
        "alternative_analyze_timeout_seconds": 1200,
        "backpressure_max_waiting": 20,
        "backpressure_max_failed": 3,
        "frequency_minutes": 60,
        "active_from_local": "08:30",
        "active_until_local": "20:30",
        "last_run_at": None,
        "last_status": None,
        "last_detail": None,
        "next_run_at": "2099-01-01T00:00:00+00:00",
        "health_status": "ok",
        "health_label": "정상",
        "health_detail": "다음 예정 시각을 대기 중입니다.",
        "manual_trigger_requested_at": None,
        "updated_by": None,
        "updated_at": None,
    }
