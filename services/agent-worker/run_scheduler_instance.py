"""Scheduler instance backed by backend DB collection_schedules config.

This process is a trigger orchestrator. It reads one schedule row from the
backend database, decides whether the schedule is due, triggers existing worker
entrypoints, and writes run state back to the control table. Collection,
normalization, analysis, aggregation, synthesis, and publishing stay inside
agent-worker handlers and daemons.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

# 워커 프로세스가 dry-run 라우트(app/api/routes/schedules.py)에서 이 모듈을 import 하므로
# import 시점 부작용을 두지 않는다 — bootstrap()/logging.basicConfig() 는 main() 에서만 실행.
# mvp_runtime import 를 위한 자기 디렉터리 경로 추가만 수행한다(모듈은 1회만 import 되고,
# 스크립트 실행 시엔 어차피 스크립트 디렉터리가 sys.path[0] 이라 사실상 no-op).
sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker

from mvp_runtime import bootstrap, load_env

logger = logging.getLogger("scheduler_instance")

_SERVICE_DIR = Path(__file__).resolve().parent  # services/agent-worker

DEFAULT_BASE_URL = "http://localhost:8011"
DEFAULT_SCHEDULE_NAME = ""
DEFAULT_REPORT_LIMIT = 100
DEFAULT_REPORT_DAYS_BACK = 7
DEFAULT_REPORT_MAX_PAGES = 20
DEFAULT_PRIORITY = "batch"
DEFAULT_ALTERNATIVE_COLLECT_TIMEOUT_SECONDS = 3600.0
DEFAULT_ALTERNATIVE_ANALYZE_TIMEOUT_SECONDS = 3600.0
DEFAULT_ALTERNATIVE_COLLECT_ENABLED = True
DEFAULT_ALTERNATIVE_ANALYZE_ENABLED = True
DEFAULT_BACKPRESSURE_MAX_WAITING = 1000
DEFAULT_BACKPRESSURE_MAX_FAILED = 100
# failed backpressure 윈도우(분) — processing_queue.failed 는 종착 상태라 총계는 평생
# 누적이므로, 최근 윈도우의 실패만 backpressure 근거로 삼는다(영구 홀드 방지).
DEFAULT_BACKPRESSURE_FAILED_WINDOW_MINUTES = 360
DEFAULT_QUEUE_STATS_TIMEOUT_SECONDS = 30.0
# 스케줄별 advisory lock 네임스페이스(classid). objid=hashtext(schedule_name) 와 2-인자형으로
# 조합해 같은 스케줄만 인스턴스 간 직렬화한다 — 과거의 단일 전역 키는 alternative
# 서브프로세스(최대 수 시간)가 도는 동안 무관한 스케줄까지 전부 블록했다.
_SCHEDULER_ADVISORY_LOCK_CLASS = 0x53434844


class CommandRunner(Protocol):
    async def __call__(self, argv: list[str], *, timeout: float) -> dict[str, Any]: ...


async def _build_backend_pool(max_pool_size: int = 4) -> Any:
    """Build the backend DB pool used for schedule config and state."""
    from signal_alpha_data_access import DatabaseSettings, create_pool

    dsn = os.getenv("BACKEND_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("BACKEND_DATABASE_URL or DATABASE_URL is required for the scheduler.")
    return await create_pool(
        DatabaseSettings(database_url=dsn, min_pool_size=1, max_pool_size=max_pool_size)
    )


def _internal_headers() -> dict[str, str]:
    """Shared token header for worker /internal/* calls."""
    token = os.getenv("INTERNAL_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("INTERNAL_API_TOKEN is required for scheduler /internal/* calls.")
    return {"X-Internal-Token": token}


async def _try_schedule_lock(connection: Any, schedule_name: str) -> bool:
    """스케줄별 advisory lock 시도 — hashtext(name) 는 SQL 쪽 안정 해시라 인스턴스가 달라도
    같은 이름이면 같은 키가 된다(파이썬 hash() 는 프로세스별 시드라 부적합)."""
    return bool(
        await connection.fetchval(
            "SELECT pg_try_advisory_lock($1, hashtext($2))",
            _SCHEDULER_ADVISORY_LOCK_CLASS,
            schedule_name,
        )
    )


async def _release_schedule_lock(connection: Any, schedule_name: str) -> None:
    await connection.fetchval(
        "SELECT pg_advisory_unlock($1, hashtext($2))",
        _SCHEDULER_ADVISORY_LOCK_CLASS,
        schedule_name,
    )


def _tail(text: str, *, limit: int = 2000) -> str:
    return text[-limit:]


# 결정 히스토리(collection_schedule_runs.detail / collection_schedules.last_detail)로 영구
# 저장되는 텍스트에서 시크릿을 마스킹한다 — 서브프로세스 stdout/stderr 테일과 예외 문자열에
# DART crtfc_key 등이 URL 쿼리 파라미터로 그대로 실려 남는 것을 막는다.
_REDACT_PATTERN = re.compile(
    r"(?i)(\b(?:crtfc_key|appkey|secretkey|secret|token|api_key|apikey|key|authorization)=)"
    r"[^&\s\"']+"
)


def _redact(text: str) -> str:
    """민감 키(key=value 쿼리 파라미터 스타일)의 값을 ***로 마스킹."""
    return _REDACT_PATTERN.sub(r"\1***", text)


def _coerce_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _report_policy(schedule: dict[str, Any]) -> dict[str, int | str]:
    return {
        "limit": _coerce_int(schedule.get("report_limit"), DEFAULT_REPORT_LIMIT, minimum=1, maximum=1000),
        "days_back": _coerce_int(
            schedule.get("report_days_back"),
            DEFAULT_REPORT_DAYS_BACK,
            minimum=1,
            maximum=400,
        ),
        "max_pages": _coerce_int(
            schedule.get("report_max_pages"),
            DEFAULT_REPORT_MAX_PAGES,
            minimum=1,
            maximum=200,
        ),
        "priority": DEFAULT_PRIORITY,
    }


def _dart_policy(schedule: dict[str, Any]) -> dict[str, int | str | bool]:
    return {
        "limit": _coerce_int(schedule.get("dart_limit"), 10, minimum=1, maximum=1000),
        "priority": DEFAULT_PRIORITY,
        "include_ownership": _coerce_bool(schedule.get("dart_include_ownership"), False),
        "include_financials": _coerce_bool(schedule.get("dart_include_financials"), False),
        "include_employee": _coerce_bool(schedule.get("dart_include_employee"), False),
    }


def _alternative_policy(schedule: dict[str, Any]) -> dict[str, Any]:
    return {
        "collect_enabled": _coerce_bool(
            schedule.get("alternative_collect_enabled"),
            DEFAULT_ALTERNATIVE_COLLECT_ENABLED,
        ),
        "analyze_enabled": _coerce_bool(
            schedule.get("alternative_analyze_enabled"),
            DEFAULT_ALTERNATIVE_ANALYZE_ENABLED,
        ),
        "collect_timeout": float(
            _coerce_int(
                schedule.get("alternative_collect_timeout_seconds"),
                int(DEFAULT_ALTERNATIVE_COLLECT_TIMEOUT_SECONDS),
                minimum=60,
                maximum=86400,
            )
        ),
        "analyze_timeout": float(
            _coerce_int(
                schedule.get("alternative_analyze_timeout_seconds"),
                int(DEFAULT_ALTERNATIVE_ANALYZE_TIMEOUT_SECONDS),
                minimum=60,
                maximum=86400,
            )
        ),
    }


async def _run_command(argv: list[str], *, timeout: float) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=_SERVICE_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(
            f"command timed out after {timeout:.0f}s: {' '.join(argv)}"
        ) from exc

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    # 테일은 히스토리(jsonb)에 그대로 저장되므로 시크릿(API 키 등)을 마스킹한 뒤 담는다.
    result = {
        "returncode": proc.returncode,
        "stdout_tail": _redact(_tail(stdout)),
        "stderr_tail": _redact(_tail(stderr)),
    }
    if proc.returncode != 0:
        output = _redact(_tail(stderr or stdout))
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(argv)}\n{output}")
    return result


def _scheduled_today(now: datetime, run_at: Any) -> datetime:
    """Return today's local scheduled timestamp in the same timezone as now."""
    return now.replace(hour=run_at.hour, minute=run_at.minute, second=0, microsecond=0)


def _next_run_at(now: datetime, run_at: Any) -> datetime:
    """Return the next local scheduled timestamp after now."""
    if isinstance(run_at, dict):
        schedule = run_at
        frequency_minutes = _frequency_minutes(schedule)
        if frequency_minutes < 1440:
            return _next_interval_run_at(now, schedule, frequency_minutes)
        run_at = schedule["run_at_local"]
    candidate = _scheduled_today(now, run_at)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _frequency_minutes(schedule: dict[str, Any]) -> int:
    try:
        minutes = int(schedule.get("frequency_minutes") or 1440)
    except (TypeError, ValueError):
        return 1440
    return max(1, minutes)


def _local_dt(value: datetime, now: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=now.tzinfo)
    return value.astimezone(now.tzinfo)


def _active_window(schedule: dict[str, Any], now: datetime) -> tuple[datetime, datetime | None]:
    start_time = schedule.get("active_from_local") or schedule["run_at_local"]
    end_time = schedule.get("active_until_local")
    start = _scheduled_today(now, start_time)
    end = _scheduled_today(now, end_time) if end_time is not None else None
    if end is not None and end < start:
        if now <= end:
            start -= timedelta(days=1)
        else:
            end += timedelta(days=1)
    return start, end


def _inside_active_window(schedule: dict[str, Any], now: datetime) -> bool:
    start, end = _active_window(schedule, now)
    if now < start:
        return False
    return end is None or now <= end


def _next_window_start(schedule: dict[str, Any], now: datetime) -> datetime:
    start, end = _active_window(schedule, now)
    if now < start:
        return start
    if end is None or now <= end:
        # 현재 윈도우 안(열린 윈도우 active_until_local=NULL 포함)에서는 오늘의 start 가
        # 현재 윈도우 시작이다. 열린 윈도우를 내일 start 로 넘기면 _should_fire 의
        # last_local < window_start 가 항상 참이 되어 인터벌 스케줄이 매 폴마다 재발화한다.
        return start
    return _scheduled_today(
        now + timedelta(days=1),
        schedule.get("active_from_local") or schedule["run_at_local"],
    )


def _next_interval_run_at(
    now: datetime,
    schedule: dict[str, Any],
    frequency_minutes: int,
) -> datetime:
    start, end = _active_window(schedule, now)
    if now < start:
        return start
    if end is not None and now >= end:
        return _scheduled_today(
            now + timedelta(days=1),
            schedule.get("active_from_local") or schedule["run_at_local"],
        )
    candidate = now + timedelta(minutes=frequency_minutes)
    if end is not None and candidate > end:
        return _scheduled_today(
            now + timedelta(days=1),
            schedule.get("active_from_local") or schedule["run_at_local"],
        )
    return candidate


def _should_fire(schedule: dict[str, Any], now: datetime) -> tuple[bool, str]:
    """Return whether the schedule should fire and why."""
    if not schedule.get("enabled"):
        return False, "disabled"
    last_run_at = schedule.get("last_run_at")
    manual_at = schedule.get("manual_trigger_requested_at")
    if manual_at is not None and (last_run_at is None or manual_at > last_run_at):
        return True, "manual"
    frequency_minutes = _frequency_minutes(schedule)
    if frequency_minutes < 1440:
        if not _inside_active_window(schedule, now):
            return False, "outside-window"
        if last_run_at is None:
            return True, "scheduled"
        last_local = _local_dt(last_run_at, now)
        window_start = _next_window_start(schedule, now)
        if last_local < window_start:
            return True, "scheduled"
        due_at = last_local + timedelta(minutes=frequency_minutes)
        if now >= due_at:
            return True, "scheduled"
        return False, "not-due"
    todays = _scheduled_today(now, schedule["run_at_local"])
    if now >= todays and (last_run_at is None or last_run_at < todays):
        return True, "scheduled"
    return False, "not-due"


async def _fire(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    schedule: dict[str, Any],
    command_runner: CommandRunner = _run_command,
) -> dict[str, Any]:
    """Trigger worker entrypoints by target and return a per-target summary."""
    base = base_url.rstrip("/")
    headers = _internal_headers()
    summary: dict[str, Any] = {}
    targets = schedule.get("targets") or []

    async def _post(path: str, payload: dict[str, Any]) -> Any:
        resp = await client.post(f"{base}{path}", json=payload, headers=headers, timeout=120.0)
        resp.raise_for_status()
        return resp.json()

    if "dart" in targets:
        try:
            dart = await _post(
                "/internal/schedules/dart/collect",
                _dart_policy(schedule),
            )
            summary["dart"] = dart.get("scheduled_count") if isinstance(dart, dict) else dart
        except Exception as exc:  # noqa: BLE001 - one target must not block the rest
            logger.warning("dart/collect failed: %s", exc)
            summary["dart"] = f"error: {_redact(str(exc))}"

    if "report" in targets:
        try:
            report = await _post(
                "/internal/schedules/report/collect",
                _report_policy(schedule),
            )
            summary["report"] = (
                report.get("scheduled_count") if isinstance(report, dict) else report
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("report/collect failed: %s", exc)
            summary["report"] = f"error: {_redact(str(exc))}"

    if "alternative" in targets:
        alternative_summary: dict[str, Any] = {}
        policy = _alternative_policy(schedule)
        if policy["collect_enabled"]:
            try:
                alternative_summary["collect"] = await command_runner(
                    [sys.executable, "run_collectors.py"],
                    timeout=policy["collect_timeout"],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("alternative collect command failed: %s", exc)
                alternative_summary["collect"] = f"error: {_redact(str(exc))}"
        else:
            alternative_summary["collect"] = "skipped: disabled"

        if policy["analyze_enabled"]:
            try:
                alternative_summary["analyze"] = await command_runner(
                    [sys.executable, "run_analyzers.py"],
                    timeout=policy["analyze_timeout"],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("alternative analyze command failed: %s", exc)
                alternative_summary["analyze"] = f"error: {_redact(str(exc))}"
        else:
            alternative_summary["analyze"] = "skipped: disabled"

        summary["alternative"] = alternative_summary

    if "price" in targets:
        price_summary: dict[str, Any] = {}
        for mode in schedule.get("price_modes") or ["flows", "snapshot"]:
            try:
                res = await _post("/internal/price/collect", {"mode": mode})
                price_summary[mode] = "ok" if isinstance(res, dict) else res
            except Exception as exc:  # noqa: BLE001
                logger.warning("price/collect(%s) failed: %s", mode, exc)
                price_summary[mode] = f"error: {_redact(str(exc))}"
        summary["price"] = price_summary

    return summary


def _summary_entry_failed(value: Any) -> bool:
    """타깃 결과 하나가 실패인지 — 구조화된 필드만 본다.

    과거의 repr(summary) 부분 문자열 매칭은 수집기 로그(stdout_tail)에 "error:" 가 섞이기만
    해도 성공 런을 partial 로 뒤집었다. 커맨드 결과는 returncode 로만 판단하고(테일 내용은
    검사하지 않음), 문자열은 우리가 직접 만드는 실패 마커("error: ...") 프리픽스만 본다.
    """
    if isinstance(value, str):
        return value.startswith("error:")
    if isinstance(value, dict):
        if "returncode" in value:
            return value.get("returncode") not in (0, None)
        return any(_summary_entry_failed(child) for child in value.values())
    return False


def _overall_status(summary: dict[str, Any]) -> str:
    """Summarize target results as noop, partial, or ok."""
    if not summary:
        return "noop"
    if "error" in summary:
        return "partial"
    return "partial" if any(_summary_entry_failed(value) for value in summary.values()) else "ok"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _backpressure_limits() -> tuple[int, int]:
    # 의도된 threshold 의미(변경하지 않음): 비교는 배타적(>) — waiting == max 는 아직
    # 발화한다. 값 0 은 해당 축의 backpressure 를 비활성화한다.
    return (
        _int_env("SCHEDULER_BACKPRESSURE_MAX_WAITING", DEFAULT_BACKPRESSURE_MAX_WAITING),
        _int_env("SCHEDULER_BACKPRESSURE_MAX_FAILED", DEFAULT_BACKPRESSURE_MAX_FAILED),
    )


def _failed_window_minutes() -> int:
    """failed backpressure 윈도우(분) — /internal/stats/queue 에 쿼리 파라미터로 전달."""
    minutes = _int_env(
        "SCHEDULER_BACKPRESSURE_FAILED_WINDOW_MINUTES",
        DEFAULT_BACKPRESSURE_FAILED_WINDOW_MINUTES,
    )
    return max(1, minutes)


def _schedule_backpressure_limits(schedule: dict[str, Any]) -> tuple[int, int]:
    default_waiting, default_failed = _backpressure_limits()
    return (
        _coerce_int(
            schedule.get("backpressure_max_waiting"),
            default_waiting,
            minimum=0,
            maximum=1_000_000,
        ),
        _coerce_int(
            schedule.get("backpressure_max_failed"),
            default_failed,
            minimum=0,
            maximum=1_000_000,
        ),
    )


# 스케줄 target → processing_queue.task_type 부분 문자열(대소문자 무시) 매핑.
# enqueue 지점 명명(app/orchestrator/queue/task_types.py): DART/REPORT/price 계열은 소문자
# (collect_dart/normalize_dart/…, collect_report/process_report/normalize_report/analyze_report, analyze_price),
# alternative 는 대문자 소스명(NORMALIZE_/ENRICH_/ANALYZE_ × HIRING/PATENT/DATALAB).
# 소스명 부분 일치라 같은 소스의 새 스테이지가 추가돼도 대체로 자동으로 따라온다.
_TARGET_TASK_TYPE_SUBSTRINGS: dict[str, tuple[str, ...]] = {
    "dart": ("dart",),
    "report": ("report",),
    "price": ("price",),
    "alternative": ("hiring", "patent", "datalab"),
}


def _waiting_count(queue_stats: dict[str, Any], targets: list[str] | None) -> int:
    """pending+retrying 적체 — 가능하면 스케줄 targets 와 관련된 task_type 만 센다.

    글로벌 총계로 재면 한 소스의 백로그(예: DART)가 무관한 소스(price 등)의 스케줄까지
    굶긴다. items(task_type×status 매트릭스)가 없거나(구버전 워커 응답) 매핑을 모르는
    target 이 섞여 있으면 보수적으로 기존 글로벌 총계로 폴백한다.
    """
    totals = queue_stats.get("totals_by_status") or {}
    global_waiting = int(totals.get("pending") or 0) + int(totals.get("retrying") or 0)
    if not targets:
        return global_waiting
    substrings: list[str] = []
    for target in targets:
        mapped = _TARGET_TASK_TYPE_SUBSTRINGS.get(str(target).lower())
        if mapped is None:
            return global_waiting
        substrings.extend(mapped)
    items = queue_stats.get("items")
    if not isinstance(items, list):
        return global_waiting
    waiting = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("status")) not in {"pending", "retrying"}:
            continue
        task_type = str(item.get("task_type") or "").lower()
        if any(marker in task_type for marker in substrings):
            waiting += int(item.get("count") or 0)
    return waiting


def _failed_count(queue_stats: dict[str, Any]) -> int:
    """backpressure 용 실패 수 — failed 는 종착 상태(자동 정리 없음)라 totals 누적치는 한 번
    임계를 넘으면 모든 수집을 영구 홀드시킨다. 워커가 윈도우 카운트(failed_recent)를 주면
    그걸 쓰고, 구버전 워커 응답(롤링 배포 호환)엔 키가 없으므로 기존 총계로 폴백한다.
    """
    if "failed_recent" in queue_stats:
        return int(queue_stats.get("failed_recent") or 0)
    totals = queue_stats.get("totals_by_status") or {}
    return int(totals.get("failed") or 0)


def _backpressure_state(
    queue_stats: dict[str, Any],
    *,
    max_waiting: int,
    max_failed: int,
    targets: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "reason": _backpressure_reason(
            queue_stats,
            max_waiting=max_waiting,
            max_failed=max_failed,
            targets=targets,
        ),
        "max_waiting": max_waiting,
        "max_failed": max_failed,
        "waiting": _waiting_count(queue_stats, targets),
        "failed": _failed_count(queue_stats),
    }


def _backpressure_reason(
    queue_stats: dict[str, Any],
    *,
    max_waiting: int,
    max_failed: int,
    targets: list[str] | None = None,
) -> str | None:
    # threshold 의미(변경하지 않음): 배타 비교(>) — 경계값은 발화, 0 은 비활성화.
    if max_waiting > 0 and _waiting_count(queue_stats, targets) > max_waiting:
        return "queue-backlog"
    if max_failed > 0 and _failed_count(queue_stats) > max_failed:
        return "recent-failures"
    return None


async def _fetch_queue_stats(client: httpx.AsyncClient, *, base_url: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    resp = await client.get(
        f"{base}/internal/stats/queue",
        headers=_internal_headers(),
        params={"failed_window_minutes": _failed_window_minutes()},
        timeout=DEFAULT_QUEUE_STATS_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else {}


def _scheduler_decision(
    schedule: dict[str, Any],
    *,
    action: str,
    reason: str,
) -> dict[str, Any]:
    """Return the scheduler agent's decision for one schedule evaluation."""
    return {
        "agent": "scheduler",
        "policy": "scheduler-agent-v1",
        "action": action,
        "reason": reason,
        "schedule_id": int(schedule["id"]),
        "schedule_name": str(schedule.get("name") or schedule["id"]),
        "targets": list(schedule.get("targets") or []),
    }


def _evaluate_schedule(
    schedule: dict[str, Any],
    now: datetime,
) -> tuple[bool, dict[str, Any]]:
    should_fire, reason = _should_fire(schedule, now)
    return should_fire, _scheduler_decision(
        schedule,
        action="fire" if should_fire else "skip",
        reason=reason,
    )


def _scheduler_dry_run(
    schedule: dict[str, Any],
    *,
    now: datetime | None = None,
    queue_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tz = ZoneInfo(schedule.get("timezone") or "Asia/Seoul")
    evaluated_at = now.astimezone(tz) if now is not None else datetime.now(tz)
    should_fire, decision = _evaluate_schedule(schedule, evaluated_at)
    max_waiting, max_failed = _schedule_backpressure_limits(schedule)
    backpressure = {
        "reason": None,
        "max_waiting": max_waiting,
        "max_failed": max_failed,
        "waiting": None,
        "failed": None,
    }
    if queue_stats is not None:
        backpressure = _backpressure_state(
            queue_stats,
            max_waiting=max_waiting,
            max_failed=max_failed,
            targets=list(schedule.get("targets") or []),
        )
        if decision["reason"] == "manual":
            backpressure["reason"] = None
    if should_fire and decision["reason"] != "manual" and backpressure["reason"] is not None:
        should_fire = False
        decision = _scheduler_decision(
            schedule,
            action="skip",
            reason=str(backpressure["reason"]),
        )
    return {
        "would_fire": should_fire,
        "evaluated_at": evaluated_at.isoformat(),
        "decision": decision,
        "next_run_at": _next_run_at(evaluated_at, schedule).isoformat(),
        "backpressure": backpressure,
        "policy": {
            "dart": _dart_policy(schedule),
            "report": _report_policy(schedule),
            "alternative": _alternative_policy(schedule),
        },
    }


def _run_detail(
    *,
    decision: dict[str, Any],
    target_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "decision": decision,
        "targets": target_summary,
    }


# 스킵 히스토리 스팸 억제: backpressure/raced 스킵을 매 폴(기본 30s)마다 히스토리에 남기면
# 하루 ~2880행이 쌓인다. 같은 사유의 연속 스킵은 첫 번째만 기록하고, 사유가 바뀌거나 실제
# 발화가 있으면 리셋한다. 프로세스 로컬(인메모리)이라 재시작 시 1회 다시 기록됨(허용).
# last_run_at 은 스킵에서 전진하지 않는다(발화 판정 불변).
_last_skip_reason_by_schedule: dict[int, str] = {}


async def _run_one_schedule(
    repo: Any,
    client: httpx.AsyncClient,
    *,
    base_url: str,
    schedule: dict[str, Any],
    decision: dict[str, Any],
    now: datetime,
) -> str:
    trigger_reason = str(decision["reason"])
    logger.info(
        "schedule firing (%s): name=%s targets=%s",
        trigger_reason,
        schedule.get("name"),
        schedule.get("targets"),
    )
    run_row = await repo.start_run(
        schedule_id=int(schedule["id"]),
        schedule_name=str(schedule.get("name") or schedule["id"]),
        trigger_reason=trigger_reason,
        targets=list(schedule.get("targets") or []),
    )
    try:
        summary = await _fire(client, base_url=base_url, schedule=schedule)
    except Exception as exc:  # noqa: BLE001 - close the run history row deterministically
        logger.exception("schedule firing failed")
        summary = {"error": _redact(str(exc))}
    status = _overall_status(summary)
    detail = _run_detail(decision=decision, target_summary=summary)
    # 실제 발화가 있었으니 스킵 억제 상태 리셋(다음 스킵은 다시 1회 기록된다).
    _last_skip_reason_by_schedule.pop(int(schedule["id"]), None)
    try:
        try:
            await repo.record_run(
                schedule_id=int(schedule["id"]),
                last_run_at=now,
                last_status=status,
                last_detail=detail,
                next_run_at=_next_run_at(now, schedule),
            )
        except Exception:  # noqa: BLE001 - 일시 장애일 수 있어 한 번만 재시도
            logger.warning("record_run failed; retrying once", exc_info=True)
            await repo.record_run(
                schedule_id=int(schedule["id"]),
                last_run_at=now,
                last_status=status,
                last_detail=detail,
                next_run_at=_next_run_at(now, schedule),
            )
        return status
    except Exception as exc:  # noqa: BLE001 - record_run 재시도까지 실패
        # last_run_at 이 전진하지 못했으니 다음 사이클에 같은 스케줄이 재발화(중복 수집)한다.
        # 히스토리를 "ok" 로 남기면 그 흔적을 추적할 수 없으므로 status=error 로 예외를
        # 함께 남긴다.
        logger.exception("record_run failed after retry")
        status = "error"
        detail = {**detail, "record_run_error": _redact(str(exc))}
        return status
    finally:
        await repo.finish_run(
            run_id=int(run_row["id"]),
            status=status,
            detail=detail,
        )
        logger.info(
            "schedule run completed: name=%s status=%s summary=%s",
            schedule.get("name"),
            status,
            summary,
        )


async def _record_skip_schedule(
    repo: Any,
    *,
    schedule: dict[str, Any],
    decision: dict[str, Any],
) -> bool:
    """스킵 히스토리 기록 — 직전과 같은 사유의 연속 스킵은 억제. 기록했으면 True."""
    schedule_id = int(schedule["id"])
    reason = str(decision["reason"])
    if _last_skip_reason_by_schedule.get(schedule_id) == reason:
        return False
    _last_skip_reason_by_schedule[schedule_id] = reason
    run_row = await repo.start_run(
        schedule_id=schedule_id,
        schedule_name=str(schedule.get("name") or schedule["id"]),
        trigger_reason=reason,
        targets=list(schedule.get("targets") or []),
    )
    await repo.finish_run(
        run_id=int(run_row["id"]),
        status="skipped",
        detail=_run_detail(decision=decision, target_summary={}),
    )
    return True


async def run_cycle(
    pool: Any,
    client: httpx.AsyncClient,
    *,
    base_url: str,
    schedule_name: str,
) -> str:
    """Run one scheduler evaluation cycle."""
    from signal_alpha_data_access.backend import CollectionScheduleRepository, parse_schedule_row

    async with pool.acquire() as connection:
        repo = CollectionScheduleRepository(connection)
        if schedule_name:
            row = await repo.get_by_name(schedule_name)
            if row is None:
                # 명시 설정된 이름이 DB에 없으면 primary 폴백 금지 — SCHEDULE_NAME 오타가
                # 조용히 엉뚱한(primary) 스케줄을 돌리는 사고를 막는다. 이름 미설정(빈값)
                # 기본 운용은 list_all 로 전체 행을 돈다.
                logger.error(
                    "collection_schedules row not found for configured name=%s; skipping cycle",
                    schedule_name,
                )
                return "schedule-not-found"
            rows = [row]
        else:
            rows = await repo.list_all()

        schedules = [
            schedule
            for schedule in (parse_schedule_row(row) for row in rows)
            if schedule is not None
        ]
        if not schedules:
            logger.warning("collection_schedules row not found (name=%s)", schedule_name or "*")
            return "no-schedule"

        due: list[tuple[dict[str, Any], dict[str, Any], datetime]] = []
        skipped_reasons: list[str] = []
        for schedule in schedules:
            tz = ZoneInfo(schedule.get("timezone") or "Asia/Seoul")
            now = datetime.now(tz)
            fire, decision = _evaluate_schedule(schedule, now)
            if fire:
                due.append((schedule, decision, now))
            else:
                skipped_reasons.append(str(decision["reason"]))

        if not due:
            if schedule_name and skipped_reasons:
                return skipped_reasons[0]
            return "not-due"

        queue_stats: dict[str, Any] | None = None
        backpressure_due: list[tuple[dict[str, Any], dict[str, Any], datetime]] = []
        for schedule, decision, now in due:
            if decision["reason"] != "manual":
                max_waiting, max_failed = _schedule_backpressure_limits(schedule)
                if queue_stats is None:
                    try:
                        queue_stats = await _fetch_queue_stats(client, base_url=base_url)
                    except Exception as exc:  # noqa: BLE001 - stats outage must not stop schedules
                        logger.warning("queue stats unavailable; skipping backpressure: %s", exc)
                        queue_stats = {}
                reason = _backpressure_reason(
                    queue_stats,
                    max_waiting=max_waiting,
                    max_failed=max_failed,
                    targets=list(schedule.get("targets") or []),
                )
                if reason is not None:
                    skipped_reasons.append(reason)
                    skip_decision = _scheduler_decision(
                        schedule,
                        action="skip",
                        reason=reason,
                    )
                    await _record_skip_schedule(
                        repo,
                        schedule=schedule,
                        decision=skip_decision,
                    )
                    logger.info(
                        "schedule skipped by backpressure: name=%s reason=%s",
                        schedule.get("name"),
                        reason,
                    )
                    continue
            backpressure_due.append((schedule, decision, now))
        due = backpressure_due

        if not due:
            if skipped_reasons:
                return skipped_reasons[0]
            return "not-due"

        statuses: list[str] = []
        fired_reasons: list[str] = []
        for schedule, decision, now in due:
            lock_name = str(schedule.get("name") or schedule["id"])
            # 스케줄별 advisory lock — 같은 스케줄만 인스턴스 간 직렬화하고, 다른 스케줄의
            # 발화(특히 장시간 alternative 서브프로세스)는 서로 막지 않는다.
            if not await _try_schedule_lock(connection, lock_name):
                logger.warning("schedule advisory lock is held elsewhere: name=%s", lock_name)
                skipped_reasons.append("lock-held")
                continue
            try:
                # TOCTOU 재확인: due 평가는 락 밖에서 했으므로, 락을 딴 시점엔 다른
                # 인스턴스가 이미 발화해 last_run_at 이 전진했을 수 있다. 행을 다시 읽어
                # 재평가하고, 더는 due 가 아니면 "raced" 로 스킵한다(price HTTP 수집·
                # alternative 서브프로세스는 자체 dedupe 가 없어 이중 발화 = 중복 수집).
                fresh = parse_schedule_row(await repo.get_by_id(int(schedule["id"])))
                if fresh is None:
                    skipped_reasons.append("raced")
                    continue
                recheck_tz = ZoneInfo(fresh.get("timezone") or "Asia/Seoul")
                recheck_now = datetime.now(recheck_tz)
                still_due, fresh_decision = _evaluate_schedule(fresh, recheck_now)
                if not still_due:
                    skipped_reasons.append("raced")
                    raced_decision = _scheduler_decision(fresh, action="skip", reason="raced")
                    await _record_skip_schedule(repo, schedule=fresh, decision=raced_decision)
                    logger.info(
                        "schedule skipped: raced by another instance: name=%s", lock_name
                    )
                    continue
                status = await _run_one_schedule(
                    repo,
                    client,
                    base_url=base_url,
                    schedule=fresh,
                    decision=fresh_decision,
                    now=recheck_now,
                )
                statuses.append(status)
                fired_reasons.append(str(fresh_decision["reason"]))
            finally:
                await _release_schedule_lock(connection, lock_name)

        if not statuses:
            if skipped_reasons:
                return skipped_reasons[0]
            return "not-due"
        if schedule_name:
            return f"fired:{fired_reasons[0]}:{statuses[0]}"
        overall = "partial" if any(status != "ok" for status in statuses) else "ok"
        return f"fired:{len(statuses)}:{overall}"


# 스케줄러 liveness 하트비트 파일. 스케줄러는 HTTP 서버가 없어 k8s httpGet 프로브를 못 쓴다 →
# 매 폴링 반복마다 이 파일 mtime 을 갱신하고, k8s exec 프로브가 파일이 임계보다 오래되면(행/크래시)
# pod 를 재시작한다(무감지 정지 차단).
HEARTBEAT_FILE = os.getenv("SCHEDULER_HEARTBEAT_FILE", "/tmp/scheduler.heartbeat")


def _touch_heartbeat() -> None:
    try:
        Path(HEARTBEAT_FILE).write_text(
            datetime.now(timezone.utc).isoformat(timespec="seconds"), encoding="utf-8"
        )
    except OSError:
        logger.warning("scheduler heartbeat write failed: %s", HEARTBEAT_FILE)


async def main() -> None:
    # import 시점 부작용 금지 — 워커 dry-run 라우트가 이 모듈을 import 하므로
    # bootstrap()/logging 설정은 스크립트 엔트리포인트(main)에서만 실행한다.
    bootstrap()
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="Signal Alpha scheduler: poll collection_schedules and trigger collection."
    )
    parser.add_argument("--once", action="store_true", help="Evaluate once and exit.")
    parser.add_argument("--poll-seconds", type=int, default=30, help="Poll interval seconds.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("WORKER_BASE_URL", DEFAULT_BASE_URL),
        help=f"Worker base URL. Defaults to env WORKER_BASE_URL or {DEFAULT_BASE_URL}.",
    )
    parser.add_argument(
        "--schedule-name",
        default=os.getenv("SCHEDULE_NAME", DEFAULT_SCHEDULE_NAME),
        help="Schedule row name. Defaults to all configured rows.",
    )
    args = parser.parse_args()

    load_env()
    pool = await _build_backend_pool()
    try:
        async with httpx.AsyncClient() as client:
            while True:
                # 루프가 살아있음을 증명(하트비트). run_cycle 이 던져도 이 시점은 지났으므로
                # 반복이 도는 한 하트비트는 신선하고, 행에 빠지면 정체 → exec 프로브가 재시작.
                _touch_heartbeat()
                try:
                    await run_cycle(
                        pool,
                        client,
                        base_url=args.base_url,
                        schedule_name=args.schedule_name,
                    )
                except Exception:  # noqa: BLE001 - scheduler loop must survive cycle failures
                    logger.exception("scheduler cycle failed; retrying next interval")
                if args.once:
                    break
                await asyncio.sleep(args.poll_seconds)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
