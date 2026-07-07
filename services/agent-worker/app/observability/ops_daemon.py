"""Hiring 운영 알림 + self-healing 데몬 (Phase 5).

agent-worker 프로세스 안에서 FastAPI lifespan 백그라운드 태스크로 돈다
(price collector 와 동일 패턴). 매 틱마다:
  1. self-healing — sweep_stale_active_tasks(좀비 active 청소) + reconcile_failed
     (미아카이브 failed 백스탑)을 자동 실행.
  2. 알림 — collector_runs 통계로 임계(거부율/전건실패/침묵실패)를 판정해 새로
     끝난 run 만 Discord Embed 로 1회 알림(run_id de-dup + cold-start warm-up).

단일 uvicorn 워커 전제 — Postgres advisory lock 으로 중복 기동을 막는다
(price 데몬과 다른 lock key). seen 상태는 메모리 보관(단일 인스턴스라 안전).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import Settings
from app.observability.alerting import (
    build_queue_health_embed,
    build_run_alert_embed,
    send_discord_alert,
)
from app.observability.stats import RunStats

logger = logging.getLogger("hiring_ops_daemon")

# price(0x50524943 "PRIC")와 겹치지 않는 키 — "OPSH".
_OPS_ADVISORY_LOCK_KEY = 0x4F505348
_RESTART_DELAY_SEC = 60.0


def _counts(row: Any) -> dict[str, int]:
    return {
        "collected": row["collected_count"] or 0,
        "inserted": row["inserted_count"] or 0,
        "skipped": row["skipped_count"] or 0,
        "failed": row["failed_count"] or 0,
    }


def _alert_reason(row: Any, settings: Settings) -> str | None:
    """run 1건의 임계 위반 사유를 반환(없으면 None) — 순수 함수.

    - status == 'failed'            → 전건 실패.
    - 거부율(failed/collected) ≥ 임계 → 거부율 초과.
    - 침묵 실패: success/partial 인데 collected>0 이고 inserted==0 → 신규 0건.
    """
    status = row["status"]
    c = _counts(row)
    collected, inserted, failed = c["collected"], c["inserted"], c["failed"]

    if status == "failed":
        return "전건 실패(run status=failed)"
    if collected > 0 and failed / collected >= settings.hiring_alert_failure_rate_threshold:
        pct = round(failed / collected * 100, 1)
        return f"거부율 {pct}% (임계 {round(settings.hiring_alert_failure_rate_threshold * 100)}%) 초과"
    if status in ("success", "partial") and collected > 0 and inserted == 0:
        return "신규 적재 0건(침묵 실패 의심)"
    return None


def _new_finished_runs(rows: list[Any], *, since_id: int) -> list[Any]:
    """since_id 초과의 '완료된'(running 아님) run 만, id 오름차순으로 반환."""
    fresh = [r for r in rows if r["id"] > since_id and r["status"] != "running"]
    return sorted(fresh, key=lambda r: r["id"])


def _queue_stall_reason(
    *, backlog: int, failed_recent: int, prev_backlog: int, settings: Settings
) -> str | None:
    """파이프라인 큐 정지/적체 사유(없으면 None) — 순수 함수.

    - 백로그(pending+retrying)가 임계 초과 **이면서 직전 틱 대비 미감소** → 드레인 정지 의심.
      (초과만으로 알리지 않는 이유: 대량 배치 직후 일시 적체는 정상 — 안 줄어들 때만 문제.)
    - 최근 윈도우 실패 수가 임계 초과 → 실패 급증.
    """
    reasons: list[str] = []
    thr = settings.ops_queue_backlog_alert_threshold
    if thr > 0 and backlog >= thr and backlog >= prev_backlog:
        reasons.append(f"백로그 {backlog}건(임계 {thr}) 초과 + 미감소(드레인 정지 의심)")
    fthr = settings.ops_queue_failed_recent_alert_threshold
    if fthr > 0 and failed_recent >= fthr:
        reasons.append(
            f"최근 {settings.ops_queue_failed_window_minutes}분 실패 {failed_recent}건(임계 {fthr}) 초과"
        )
    return "; ".join(reasons) if reasons else None


async def _alert_queue_health(
    obs: Any, settings: Settings, http: httpx.AsyncClient, seen: dict[str, int]
) -> None:
    """processing_queue 전역 상태로 정지/적체를 판정해 1회 알림(seen 상태로 de-dup).

    hiring 수집 run 한정 알림을 파이프라인 전역으로 넓히는 부분. 백로그가 임계 초과 + 미감소(정지)면
    한 번 알리고, 해소되면 상태를 풀어 재알림을 허용한다. 구조적 self-heal 은 /health/live·스케줄러
    하트비트가 담당하고 이 알림은 사람 인지용 보조.
    """
    if (
        settings.ops_queue_backlog_alert_threshold <= 0
        and settings.ops_queue_failed_recent_alert_threshold <= 0
    ):
        return
    rows = await obs.queue_stats()
    totals: dict[str, int] = {}
    for row in rows:
        totals[row["status"]] = totals.get(row["status"], 0) + int(row["count"])
    backlog = totals.get("pending", 0) + totals.get("retrying", 0)
    failed_recent = await obs.recent_failed_count(
        window_minutes=settings.ops_queue_failed_window_minutes
    )
    prev_backlog = seen.get("__queue_backlog__", 0)
    reason = _queue_stall_reason(
        backlog=backlog, failed_recent=failed_recent, prev_backlog=prev_backlog, settings=settings
    )
    already_alerted = seen.get("__queue_alerted__", 0)
    if reason and not already_alerted:
        embed = build_queue_health_embed(
            reason=reason, backlog=backlog, failed_recent=failed_recent, totals=totals
        )
        await send_discord_alert(http, settings.discord_webhook_url, embed)
        logger.warning("🚨 큐 정지/적체 경보: %s", reason)
        seen["__queue_alerted__"] = 1
    elif not reason and already_alerted:
        seen["__queue_alerted__"] = 0
        logger.info("큐 정지/적체 해소 — 알림 상태 리셋")
    seen["__queue_backlog__"] = backlog


async def run_ops_cycle(
    pool: Any,
    settings: Settings,
    http: httpx.AsyncClient,
    seen: dict[str, int],
) -> None:
    """데몬 1틱: self-healing(sweep/reconcile) + 임계 위반 run 알림."""
    from signal_alpha_data_access.repositories import (
        DeadLetterRepository,
        ObservabilityRepository,
        ProcessingQueueRepository,
    )

    async with pool.acquire() as conn:
        # 1) self-healing — 좀비 active 태스크 청소 + 미아카이브 failed 백스탑.
        swept = await ProcessingQueueRepository(conn).sweep_stale_active_tasks(
            running_timeout_minutes=settings.hiring_ops_sweep_running_timeout_min,
            retrying_timeout_minutes=settings.hiring_ops_sweep_retrying_timeout_min,
        )
        archived = await DeadLetterRepository(conn).reconcile_failed(
            limit=settings.hiring_ops_reconcile_limit
        )
        if swept or archived:
            logger.info("self-healing: swept=%s reconciled=%s", swept, archived)

        # 2) 알림 — 새로 끝난 run 중 임계 위반만 1회.
        obs = ObservabilityRepository(conn)
        for ctype in settings.hiring_alert_collector_types:
            rows = await obs.recent_collector_runs(limit=20, collector_type=ctype)
            if not rows:
                continue
            max_id = max(r["id"] for r in rows)

            # ── Cold start/warm-up 가드 ──────────────────────────────────────
            # 데몬 최초 기동/재배포 시 seen 은 비어 있다(메모리 상태). 이때 과거
            # 실패 run 을 "새것"으로 오인해 알림 폭탄을 쏘지 않도록, 첫 틱은 알림
            # 없이 현재 최신 run_id 로 baseline 만 채우고 다음 틱부터 평가한다.
            # (트레이드오프: 데몬이 꺼져 있던 동안의 장애 run 은 알림되지 않음 —
            #  의도된 동작. 그 구간은 stats API 로 확인. 폭탄 방지를 우선한다.)
            if ctype not in seen:
                seen[ctype] = max_id
                logger.info("ops 데몬 warm-up: %s baseline run_id=%s", ctype, max_id)
                continue

            for row in _new_finished_runs(rows, since_id=seen[ctype]):
                reason = _alert_reason(row, settings)
                if reason is None:
                    continue
                embed = build_run_alert_embed(
                    ctype,
                    RunStats.from_counts(**_counts(row)),
                    row["status"],
                    run_id=row["id"],
                    reason=reason,
                )
                await send_discord_alert(http, settings.discord_webhook_url, embed)
                logger.warning("🚨 %s run %s 경보: %s", ctype, row["id"], reason)

            seen[ctype] = max(seen[ctype], max_id)

        # 3) 파이프라인 큐 정지/적체 알림(hiring 한정 → 전역).
        await _alert_queue_health(obs, settings, http, seen)


async def run_ops_daemon(pool: Any, settings: Settings) -> None:
    """주기 루프: run_ops_cycle 을 interval 마다 실행. cancel 은 전파."""
    seen: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=10.0) as http:
        try:
            while True:
                try:
                    await run_ops_cycle(pool, settings, http, seen)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - 한 틱 실패가 데몬을 죽이지 않게
                    logger.exception("ops 데몬 틱 실패 — 다음 주기에 재시도")
                await asyncio.sleep(settings.hiring_ops_interval_sec)
        except asyncio.CancelledError:
            logger.info("ops 데몬 cancelled")
            raise


async def supervise_ops_daemon(pool: Any, settings: Settings) -> None:
    """run_ops_daemon 감시: advisory lock 단일화 + 예기치 못한 죽음이면 60초 후 재기동.

    락을 못 잡으면(다른 워커/컨테이너가 이미 가동) 폴링하지 않고 60초마다 재시도한다.
    락 보유자가 죽으면 세션이 끊겨 락이 풀리므로 자동 승계된다(price 데몬과 동일).
    """
    while True:
        try:
            async with pool.acquire() as lock_conn:
                locked = await lock_conn.fetchval(
                    "SELECT pg_try_advisory_lock($1)", _OPS_ADVISORY_LOCK_KEY
                )
                if not locked:
                    logger.warning(
                        "ops 데몬 락 보유 중인 인스턴스 존재 — %.0fs 후 재시도",
                        _RESTART_DELAY_SEC,
                    )
                else:
                    await run_ops_daemon(pool, settings)
                    logger.error("ops 데몬이 예기치 않게 종료됨 — 재기동")
        except asyncio.CancelledError:
            logger.info("ops 데몬 supervisor cancelled")
            raise
        except Exception:  # noqa: BLE001 - 데몬은 죽지 않고 재기동한다
            logger.exception("ops 데몬 crash — %.0fs 후 재기동", _RESTART_DELAY_SEC)
        await asyncio.sleep(_RESTART_DELAY_SEC)
