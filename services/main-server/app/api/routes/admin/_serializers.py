"""어드민 라우터 공용 — 행 직렬화·검증·스케줄 헬스 계산(라우트 비의존, 순수 함수)."""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime, time, timedelta
from typing import Any

from app.api.routes.admin_auth import admin_error

# 수집 스케줄 제어 허용값.
_SCHEDULE_TARGETS = {"price", "dart", "report", "alternative"}
_SCHEDULE_PRICE_MODES = {"flows", "snapshot"}
_SCHEDULE_HEALTH_GRACE_MINUTES = 15

_SCHEDULE_HEALTH_LABELS = {
    "ok": "정상",
    "disabled": "비활성",
    "delayed": "지연",
    "failed_waiting": "실패 후 대기",
    "unknown": "확인 필요",
}

# member_code = 영문 대문자 4 + 숫자 4 (혼동 문자 I/O/0/1 제외). auth._new_member_code 와 동일 규칙.
_CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_CODE_DIGITS = "23456789"
_MEMBER_CODE_RETRIES = 6
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _new_member_code() -> str:
    letters = "".join(secrets.choice(_CODE_LETTERS) for _ in range(4))
    digits = "".join(secrets.choice(_CODE_DIGITS) for _ in range(4))
    return f"{letters}{digits}"


def _audit_json(row: dict[str, Any] | None) -> str | None:
    """행 스냅샷을 감사로그 JSONB 로 직렬화(datetime 등은 문자열로)."""
    if row is None:
        return None
    serializable = {
        key: (value.isoformat() if hasattr(value, "isoformat") else value)
        for key, value in row.items()
        # 비밀번호 해시 등 민감/대용량 필드는 스냅샷에서 제외.
        if key not in ("password_hash", "raw_response")
    }
    return json.dumps(serializable, ensure_ascii=False, default=str)


def _admin_email(value: str) -> str:
    email = (value or "").strip().lower()
    if not _EMAIL_PATTERN.match(email):
        raise admin_error(400, "INVALID_EMAIL", "이메일 형식이 올바르지 않습니다.")
    return email


def _parse_schedule_time(value: str) -> time:
    try:
        hour, minute = value.strip().split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        raise admin_error(400, "INVALID_TIME", "시각 형식(HH:MM)이 올바르지 않습니다.") from None


def _validate_targets(targets: list[str]) -> list[str]:
    cleaned = [t.strip().lower() for t in targets if t and t.strip()]
    if not cleaned:
        raise admin_error(
            400,
            "EMPTY_TARGETS",
            "수집 대상을 최소 1개 선택해야 합니다.",
        )
    invalid = [t for t in cleaned if t not in _SCHEDULE_TARGETS]
    if invalid:
        raise admin_error(400, "INVALID_TARGETS", f"허용되지 않은 대상: {invalid}")
    return cleaned


def _validate_range(value: int | None, field: str, minimum: int, maximum: int) -> None:
    if value is None:
        return
    if not (minimum <= value <= maximum):
        raise admin_error(
            400,
            f"INVALID_{field.upper()}",
            f"{field} 는 {minimum}~{maximum} 이어야 합니다.",
        )


def _validate_active_window(
    *,
    frequency_minutes: Any,
    active_from: Any,
    active_until: Any,
) -> None:
    try:
        minutes = int(frequency_minutes or 1440)
    except (TypeError, ValueError):
        minutes = 1440
    if minutes >= 1440 or active_from is None or active_until is None:
        return
    if active_from == active_until:
        raise admin_error(
            400,
            "INVALID_ACTIVE_WINDOW",
            "반복 스케줄의 활성 시작/종료 시각은 같을 수 없습니다.",
        )


def _validate_price_modes(modes: list[str]) -> list[str]:
    cleaned = [m.strip().lower() for m in modes if m and m.strip()]
    invalid = [m for m in cleaned if m not in _SCHEDULE_PRICE_MODES]
    if invalid:
        raise admin_error(400, "INVALID_PRICE_MODES", f"허용되지 않은 모드: {invalid}")
    return cleaned


def _schedule_row(row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    run_at = row.get("run_at_local")
    active_from = row.get("active_from_local")
    active_until = row.get("active_until_local")
    detail = row.get("last_detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except (ValueError, TypeError):
            pass
    health = _schedule_health(row)
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "enabled": row.get("enabled"),
        "run_at_local": run_at.strftime("%H:%M") if hasattr(run_at, "strftime") else run_at,
        "timezone": row.get("timezone"),
        "targets": row.get("targets") or [],
        "dart_limit": row.get("dart_limit"),
        "price_modes": row.get("price_modes") or [],
        "report_limit": row.get("report_limit"),
        "report_days_back": row.get("report_days_back"),
        "report_max_pages": row.get("report_max_pages"),
        "alternative_collect_enabled": row.get("alternative_collect_enabled"),
        "alternative_analyze_enabled": row.get("alternative_analyze_enabled"),
        "alternative_collect_timeout_seconds": row.get(
            "alternative_collect_timeout_seconds"
        ),
        "alternative_analyze_timeout_seconds": row.get(
            "alternative_analyze_timeout_seconds"
        ),
        "backpressure_max_waiting": row.get("backpressure_max_waiting"),
        "backpressure_max_failed": row.get("backpressure_max_failed"),
        "frequency_minutes": row.get("frequency_minutes"),
        "active_from_local": (
            active_from.strftime("%H:%M") if hasattr(active_from, "strftime") else active_from
        ),
        "active_until_local": (
            active_until.strftime("%H:%M") if hasattr(active_until, "strftime") else active_until
        ),
        "last_run_at": _timestamp(row.get("last_run_at")),
        "last_status": row.get("last_status"),
        "last_detail": detail,
        "next_run_at": _timestamp(row.get("next_run_at")),
        "health_status": health["status"],
        "health_label": health["label"],
        "health_detail": health["detail"],
        "manual_trigger_requested_at": _timestamp(row.get("manual_trigger_requested_at")),
        "updated_by": row.get("updated_by"),
        "updated_at": _timestamp(row.get("updated_at")),
    }


def _as_utc_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _schedule_health(
    row: dict[str, Any],
    *,
    now: datetime | None = None,
    grace_minutes: int = _SCHEDULE_HEALTH_GRACE_MINUTES,
) -> dict[str, str]:
    status = "ok"
    detail = "다음 예정 시각을 대기 중입니다."
    if not row.get("enabled"):
        status = "disabled"
        detail = "스케줄이 비활성 상태입니다."
    else:
        now_utc = (now or datetime.now(UTC)).astimezone(UTC)
        next_run_at = _as_utc_dt(row.get("next_run_at"))
        if next_run_at is not None and now_utc > next_run_at + timedelta(minutes=grace_minutes):
            status = "delayed"
            detail = "다음 예정 시각을 지나 추가 확인이 필요합니다."
        elif str(row.get("last_status") or "").lower() in {"failed", "partial"}:
            status = "failed_waiting"
            detail = "최근 실행이 실패 또는 부분 완료 상태입니다."
        elif next_run_at is None:
            status = "unknown"
            detail = "다음 예정 시각이 없어 스케줄러 상태 확인이 필요합니다."
    return {
        "status": status,
        "label": _SCHEDULE_HEALTH_LABELS.get(status, status),
        "detail": detail,
    }


def _schedule_health_summary(schedules: list[dict[str, Any]]) -> dict[str, Any]:
    by_health_status: dict[str, int] = {}
    for schedule in schedules:
        status = str(schedule.get("health_status") or "unknown")
        by_health_status[status] = by_health_status.get(status, 0) + 1
    attention_count = sum(
        count
        for status, count in by_health_status.items()
        if status not in {"ok", "disabled"}
    )
    return {
        "total": len(schedules),
        "attention_count": attention_count,
        "by_health_status": by_health_status,
    }


def _queue_ops_events(
    queue: dict[str, Any],
    failed_tasks: dict[str, Any],
    dead_letters: dict[str, Any],
    schedule_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    totals = queue.get("totals_by_status") if isinstance(queue, dict) else {}
    totals = totals if isinstance(totals, dict) else {}
    waiting = int(totals.get("pending") or 0) + int(totals.get("retrying") or 0)
    failed = int(totals.get("failed") or 0)
    if waiting:
        events.append({
            "type": "queue_backlog",
            "severity": "warning",
            "message": f"대기 또는 재시도 중인 큐 작업이 {waiting}건 있습니다.",
            "count": waiting,
        })
    if failed:
        events.append({
            "type": "queue_failed",
            "severity": "warning",
            "message": f"실패 상태 큐 작업이 {failed}건 있습니다.",
            "count": failed,
        })
    unreplayed = 0
    dead_letter_summary = queue.get("dead_letter") if isinstance(queue, dict) else None
    if isinstance(dead_letter_summary, dict):
        unreplayed = int(dead_letter_summary.get("unreplayed") or 0)
    if not unreplayed:
        unreplayed = int(dead_letters.get("count") or 0)
    if unreplayed:
        events.append({
            "type": "dead_letter_pending",
            "severity": "critical",
            "message": f"재처리되지 않은 Dead Letter가 {unreplayed}건 있습니다.",
            "count": unreplayed,
        })
    attention_count = int(schedule_summary.get("attention_count") or 0)
    if attention_count:
        events.append({
            "type": "schedule_health",
            "severity": "warning",
            "message": f"추가 확인이 필요한 스케줄이 {attention_count}건 있습니다.",
            "count": attention_count,
        })
    failed_task_count = int(failed_tasks.get("count") or 0)
    if failed_task_count and failed_task_count != failed:
        events.append({
            "type": "failed_task_list",
            "severity": "info",
            "message": f"최근 실패 작업 목록에 {failed_task_count}건이 표시됩니다.",
            "count": failed_task_count,
        })
    return events


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return fallback
    return value


def _schedule_run_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "schedule_id": row.get("schedule_id"),
        "schedule_name": row.get("schedule_name"),
        "trigger_reason": row.get("trigger_reason"),
        "targets": _json_value(row.get("targets"), []),
        "status": row.get("status"),
        "detail": _json_value(row.get("detail"), None),
        "started_at": _timestamp(row.get("started_at")),
        "finished_at": _timestamp(row.get("finished_at")),
        "created_at": _timestamp(row.get("created_at")),
    }


def _user_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row.get("email"),
        "nickname": row.get("nickname"),
        "member_code": row.get("member_code"),
        "created_at": _timestamp(row.get("created_at")),
        "subscription": _subscription_brief(row),
    }


def _user_detail(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row.get("email"),
        "nickname": row.get("nickname"),
        "member_code": row.get("member_code"),
        "agreed_risk": row.get("agreed_risk"),
        "is_verified": row.get("is_verified"),
        "created_at": _timestamp(row.get("created_at")),
        "watchlist_count": int(row.get("watchlist_count") or 0),
        "subscription": _subscription_brief(row),
        "subscription_started_at": _timestamp(row.get("subscription_started_at")),
        "subscription_expires_at": _timestamp(row.get("subscription_expires_at")),
    }


def _subscription_brief(row: dict[str, Any]) -> dict[str, Any] | None:
    plan_type = row.get("plan_type")
    if plan_type is None:
        return None
    return {"plan_type": plan_type, "status": row.get("subscription_status")}


def _parse_dt(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise admin_error(
            400, "INVALID_DATETIME", f"{field} 형식이 올바르지 않습니다."
        ) from None


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
