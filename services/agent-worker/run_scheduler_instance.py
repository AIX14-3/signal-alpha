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
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker

from mvp_runtime import bootstrap, load_env

bootstrap()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scheduler_instance")

DEFAULT_BASE_URL = "http://localhost:8011"
DEFAULT_SCHEDULE_NAME = ""
DEFAULT_REPORT_LIMIT = 100
DEFAULT_REPORT_DAYS_BACK = 7
DEFAULT_REPORT_MAX_PAGES = 20
DEFAULT_PRIORITY = "batch"
DEFAULT_ALTERNATIVE_COLLECT_TIMEOUT_SECONDS = 3600.0
DEFAULT_ALTERNATIVE_ANALYZE_TIMEOUT_SECONDS = 3600.0
DEFAULT_BACKPRESSURE_MAX_WAITING = 1000
DEFAULT_BACKPRESSURE_MAX_FAILED = 100
DEFAULT_QUEUE_STATS_TIMEOUT_SECONDS = 30.0
_SCHEDULER_ADVISORY_LOCK_KEY = 0x53434844
_SERVICE_DIR = Path(__file__).resolve().parent


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


async def _try_scheduler_lock(connection: Any) -> bool:
    return bool(
        await connection.fetchval(
            "SELECT pg_try_advisory_lock($1)", _SCHEDULER_ADVISORY_LOCK_KEY
        )
    )


async def _release_scheduler_lock(connection: Any) -> None:
    await connection.fetchval("SELECT pg_advisory_unlock($1)", _SCHEDULER_ADVISORY_LOCK_KEY)


def _tail(text: str, *, limit: int = 2000) -> str:
    return text[-limit:]


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
    result = {
        "returncode": proc.returncode,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
    }
    if proc.returncode != 0:
        output = _tail(stderr or stdout)
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
    if end is not None and now <= end:
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
                {"limit": int(schedule.get("dart_limit") or 10), "priority": DEFAULT_PRIORITY},
            )
            summary["dart"] = dart.get("scheduled_count") if isinstance(dart, dict) else dart
        except Exception as exc:  # noqa: BLE001 - one target must not block the rest
            logger.warning("dart/collect failed: %s", exc)
            summary["dart"] = f"error: {exc}"

    if "report" in targets:
        try:
            report = await _post(
                "/internal/schedules/report/collect",
                {
                    "limit": DEFAULT_REPORT_LIMIT,
                    "days_back": DEFAULT_REPORT_DAYS_BACK,
                    "max_pages": DEFAULT_REPORT_MAX_PAGES,
                    "priority": DEFAULT_PRIORITY,
                },
            )
            summary["report"] = (
                report.get("scheduled_count") if isinstance(report, dict) else report
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("report/collect failed: %s", exc)
            summary["report"] = f"error: {exc}"

    if "alternative" in targets:
        alternative_summary: dict[str, Any] = {}
        try:
            alternative_summary["collect"] = await command_runner(
                [sys.executable, "run_collectors.py"],
                timeout=DEFAULT_ALTERNATIVE_COLLECT_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("alternative collect command failed: %s", exc)
            alternative_summary["collect"] = f"error: {exc}"

        try:
            alternative_summary["analyze"] = await command_runner(
                [sys.executable, "run_analyzers.py"],
                timeout=DEFAULT_ALTERNATIVE_ANALYZE_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("alternative analyze command failed: %s", exc)
            alternative_summary["analyze"] = f"error: {exc}"

        summary["alternative"] = alternative_summary

    if "price" in targets:
        price_summary: dict[str, Any] = {}
        for mode in schedule.get("price_modes") or ["flows", "snapshot"]:
            try:
                res = await _post("/internal/price/collect", {"mode": mode})
                price_summary[mode] = "ok" if isinstance(res, dict) else res
            except Exception as exc:  # noqa: BLE001
                logger.warning("price/collect(%s) failed: %s", mode, exc)
                price_summary[mode] = f"error: {exc}"
        summary["price"] = price_summary

    return summary


def _overall_status(summary: dict[str, Any]) -> str:
    """Summarize target results as noop, partial, or ok."""
    flat = repr(summary)
    if not summary:
        return "noop"
    return "partial" if "error" in summary or "error:" in flat else "ok"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _backpressure_limits() -> tuple[int, int]:
    return (
        _int_env("SCHEDULER_BACKPRESSURE_MAX_WAITING", DEFAULT_BACKPRESSURE_MAX_WAITING),
        _int_env("SCHEDULER_BACKPRESSURE_MAX_FAILED", DEFAULT_BACKPRESSURE_MAX_FAILED),
    )


def _backpressure_reason(
    queue_stats: dict[str, Any],
    *,
    max_waiting: int,
    max_failed: int,
) -> str | None:
    totals = queue_stats.get("totals_by_status") or {}
    waiting = int(totals.get("pending") or 0) + int(totals.get("retrying") or 0)
    failed = int(totals.get("failed") or 0)
    if max_waiting > 0 and waiting > max_waiting:
        return "queue-backlog"
    if max_failed > 0 and failed > max_failed:
        return "recent-failures"
    return None


async def _fetch_queue_stats(client: httpx.AsyncClient, *, base_url: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    resp = await client.get(
        f"{base}/internal/stats/queue",
        headers=_internal_headers(),
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


def _run_detail(
    *,
    decision: dict[str, Any],
    target_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "decision": decision,
        "targets": target_summary,
    }


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
        summary = {"error": str(exc)}
    status = _overall_status(summary)
    detail = _run_detail(decision=decision, target_summary=summary)
    try:
        await repo.record_run(
            schedule_id=int(schedule["id"]),
            last_run_at=now,
            last_status=status,
            last_detail=detail,
            next_run_at=_next_run_at(now, schedule),
        )
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
            rows = [await repo.get_by_name(schedule_name) or await repo.get_primary()]
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

        max_waiting, max_failed = _backpressure_limits()
        queue_stats: dict[str, Any] | None = None
        backpressure_due: list[tuple[dict[str, Any], dict[str, Any], datetime]] = []
        for schedule, decision, now in due:
            if decision["reason"] != "manual":
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
                )
                if reason is not None:
                    skipped_reasons.append(reason)
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

        if not await _try_scheduler_lock(connection):
            logger.warning("scheduler advisory lock is held elsewhere")
            return "lock-held"

        try:
            statuses: list[str] = []
            for schedule, decision, now in due:
                status = await _run_one_schedule(
                    repo,
                    client,
                    base_url=base_url,
                    schedule=schedule,
                    decision=decision,
                    now=now,
                )
                statuses.append(status)
            if schedule_name:
                return f"fired:{due[0][1]['reason']}:{statuses[0]}"
            overall = "partial" if any(status != "ok" for status in statuses) else "ok"
            return f"fired:{len(statuses)}:{overall}"
        finally:
            await _release_scheduler_lock(connection)


async def main() -> None:
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
