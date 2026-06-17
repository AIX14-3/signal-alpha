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
from app.observability.alerting import build_run_alert_embed, send_discord_alert
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
