from datetime import time

from app.api.routes.admin import _schedule_row, _validate_targets


def test_validate_targets_allows_alternative_scheduler_target():
    assert _validate_targets([" alternative ", "price"]) == ["alternative", "price"]


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
            "frequency_minutes": 60,
            "active_from_local": time(8, 30),
            "active_until_local": time(20, 30),
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
        "frequency_minutes": 60,
        "active_from_local": "08:30",
        "active_until_local": "20:30",
        "last_run_at": None,
        "last_status": None,
        "last_detail": None,
        "next_run_at": None,
        "manual_trigger_requested_at": None,
        "updated_by": None,
        "updated_at": None,
    }
