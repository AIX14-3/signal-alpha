"""스케줄러 인스턴스 — 백엔드 DB `collection_schedules` config 기반 일일 수집 트리거.

팀 스케줄러 경계(`docs/runbooks/agent-pipeline-schedule.md`): **스케줄러는 수집/분석 로직을
직접 들지 않고 워커의 내부 엔드포인트만 호출**한다. 이전엔 `--interval-seconds` 고정 루프로
DART/report 만 인큐했지만, 이제 **백엔드 DB 의 단일 config 행을 폴링**해 매일 `run_at_local`
(타임존, 기본 04:30 KST)에 1회 발화하고, 어드민이 설정한 `enabled`/대상/`manual_trigger`
를 따른다. 발화 시 대상별로 워커 내부 엔드포인트를 호출한다:
  - price  → POST /internal/price/collect (mode 별: flows, snapshot)
  - dart   → POST /internal/schedules/dart/collect (limit, priority=batch)
  - report → POST /internal/schedules/report/collect (limit, days_back, max_pages, priority=batch)
인큐된 COLLECT_* 는 워커 **큐 드레인 데몬**(`QUEUE_DRAIN_DAEMON_ENABLED`)이 발행까지 소비한다.

config 는 어드민(main-server)이 `collection_schedules` 에 쓰고, 이 프로세스는 발행용
백엔드 연결(`BACKEND_DATABASE_URL`, 미설정 시 `DATABASE_URL`)로 같은 행을 읽고/상태를 쓴다 —
`main-server → worker` 직접 호출은 없다(설계도 경계 유지).

  uv run python run_scheduler_instance.py                  # 폴링 데몬(기본)
  uv run python run_scheduler_instance.py --once           # 1회 평가 후 종료(cron 호환)
  uv run python run_scheduler_instance.py --poll-seconds 30
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
DEFAULT_SCHEDULE_NAME = "daily-collection"
DEFAULT_REPORT_LIMIT = 100
DEFAULT_REPORT_DAYS_BACK = 7
DEFAULT_REPORT_MAX_PAGES = 20
DEFAULT_PRIORITY = "batch"
DEFAULT_ALTERNATIVE_COLLECT_TIMEOUT_SECONDS = 3600.0
DEFAULT_ALTERNATIVE_ANALYZE_TIMEOUT_SECONDS = 3600.0
_SERVICE_DIR = Path(__file__).resolve().parent


class CommandRunner(Protocol):
    async def __call__(self, argv: list[str], *, timeout: float) -> dict[str, Any]: ...


async def _build_backend_pool(max_pool_size: int = 4) -> Any:
    """config 폴링용 백엔드 DB 풀. BACKEND_DATABASE_URL 우선, 없으면 DATABASE_URL(단일 DB 모드)."""
    from signal_alpha_data_access import DatabaseSettings, create_pool

    dsn = os.getenv("BACKEND_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("BACKEND_DATABASE_URL or DATABASE_URL is required for the scheduler.")
    return await create_pool(
        DatabaseSettings(database_url=dsn, min_pool_size=1, max_pool_size=max_pool_size)
    )


def _internal_headers() -> dict[str, str]:
    """워커 /internal/* 호출용 공유 시크릿 헤더(설정 시). Phase 6 인증 가드와 짝."""
    token = os.getenv("INTERNAL_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("INTERNAL_API_TOKEN is required for scheduler /internal/* calls.")
    return {"X-Internal-Token": token}


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
    """now 와 같은 타임존에서 오늘의 run_at_local 시각(tz-aware)."""
    return now.replace(hour=run_at.hour, minute=run_at.minute, second=0, microsecond=0)


def _next_run_at(now: datetime, run_at: Any) -> datetime:
    """다음 발화 예정 시각 — 오늘 시각이 지났으면 내일."""
    candidate = _scheduled_today(now, run_at)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _should_fire(schedule: dict[str, Any], now: datetime) -> tuple[bool, str]:
    """(발화여부, 사유). enabled + (오늘 정시 도달·미실행) 또는 수동 트리거."""
    if not schedule.get("enabled"):
        return False, "disabled"
    last_run_at = schedule.get("last_run_at")
    manual_at = schedule.get("manual_trigger_requested_at")
    if manual_at is not None and (last_run_at is None or manual_at > last_run_at):
        return True, "manual"
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
    """대상별 워커 엔드포인트 호출. {target: 결과/에러} 요약 반환."""
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
        except Exception as exc:  # noqa: BLE001 - 한 대상 실패가 다른 대상/상태기록을 막지 않게
            logger.warning("dart/collect 실패: %s", exc)
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
        except Exception as exc:  # noqa: BLE001 - 한 대상 실패가 다른 대상/상태기록을 막지 않게
            logger.warning("report/collect 실패: %s", exc)
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
                logger.warning("price/collect(%s) 실패: %s", mode, exc)
                price_summary[mode] = f"error: {exc}"
        summary["price"] = price_summary

    return summary


def _overall_status(summary: dict[str, Any]) -> str:
    """요약에 'error:' 가 있으면 partial, 비었으면 noop, 아니면 ok."""
    flat = repr(summary)
    if not summary:
        return "noop"
    return "partial" if "error:" in flat else "ok"


async def run_cycle(
    pool: Any,
    client: httpx.AsyncClient,
    *,
    base_url: str,
    schedule_name: str,
) -> str:
    """1 폴링 주기: config 읽기 → 발화 판단 → (발화 시) 호출 + 상태 기록. 사유 반환."""
    from signal_alpha_data_access.backend import CollectionScheduleRepository, parse_schedule_row

    async with pool.acquire() as connection:
        repo = CollectionScheduleRepository(connection)
        row = await repo.get_by_name(schedule_name) or await repo.get_primary()
        schedule = parse_schedule_row(row)
        if schedule is None:
            logger.warning("collection_schedules 에 스케줄이 없습니다(name=%s).", schedule_name)
            return "no-schedule"

        tz = ZoneInfo(schedule.get("timezone") or "Asia/Seoul")
        now = datetime.now(tz)
        fire, reason = _should_fire(schedule, now)
        if not fire:
            return reason

        logger.info("스케줄 발화(%s) — targets=%s", reason, schedule.get("targets"))
        summary = await _fire(client, base_url=base_url, schedule=schedule)
        status = _overall_status(summary)
        await repo.record_run(
            schedule_id=int(schedule["id"]),
            last_run_at=now,
            last_status=status,
            last_detail=summary,
            next_run_at=_next_run_at(now, schedule["run_at_local"]),
        )
        logger.info("스케줄 실행 완료: status=%s summary=%s", status, summary)
        return f"fired:{reason}:{status}"


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Signal alpha scheduler: collection_schedules config 폴링 → 일일 수집 트리거."
    )
    parser.add_argument("--once", action="store_true", help="1회 평가 후 종료(루프 없이).")
    parser.add_argument("--poll-seconds", type=int, default=30, help="폴링 간격(초). 기본 30.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("WORKER_BASE_URL", DEFAULT_BASE_URL),
        help=f"워커 내부 URL. 기본 env WORKER_BASE_URL 또는 {DEFAULT_BASE_URL}.",
    )
    parser.add_argument(
        "--schedule-name",
        default=os.getenv("SCHEDULE_NAME", DEFAULT_SCHEDULE_NAME),
        help=f"제어할 스케줄 행 이름. 기본 {DEFAULT_SCHEDULE_NAME}.",
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
                except Exception:  # noqa: BLE001 - 한 주기 실패가 스케줄러를 죽이지 않게
                    logger.exception("스케줄러 주기 실패 — 다음 주기에 재시도")
                if args.once:
                    break
                await asyncio.sleep(args.poll_seconds)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
