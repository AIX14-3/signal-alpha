from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends

from app.core.database import get_database_pool
from app.observability import RunStats, since_to_utc_start

router = APIRouter(prefix="/internal/stats", tags=["stats"])


@router.get("/queue")
async def queue_stats(
    failed_window_minutes: int = 360,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """processing_queue 적체 현황 — task_type×status 매트릭스 + status별 합계.
    종착 실패 격리(dead_letter) 카운트도 함께 노출(Phase 2 DLQ).

    ``failed_window_minutes``: totals_by_status.failed 는 평생 누적치라 스케줄러
    backpressure 판정에 쓰면 영구 홀드가 되므로, 최근 윈도우의 실패 수(failed_recent)를
    함께 노출한다(기본 6시간).
    """
    from signal_alpha_data_access.repositories import (
        DeadLetterRepository,
        ObservabilityRepository,
    )

    failed_window_minutes = max(1, min(failed_window_minutes, 20160))  # 최대 2주
    async with pool.acquire() as connection:
        observability = ObservabilityRepository(connection)
        rows = await observability.queue_stats()
        failed_recent = await observability.recent_failed_count(
            window_minutes=failed_window_minutes
        )
        dead_letter_rows = await DeadLetterRepository(connection).dead_letter_stats()

    items = [dict(row) for row in rows]
    totals_by_status: dict[str, int] = defaultdict(int)
    for item in items:
        totals_by_status[item["status"]] += int(item["count"])

    dead_letter_items = [dict(row) for row in dead_letter_rows]
    return {
        "total": sum(totals_by_status.values()),
        "totals_by_status": dict(totals_by_status),
        "items": items,
        "failed_recent": failed_recent,
        "failed_window_minutes": failed_window_minutes,
        "dead_letter": {
            "total": sum(int(d["total"]) for d in dead_letter_items),
            "unreplayed": sum(int(d["unreplayed"]) for d in dead_letter_items),
            "items": dead_letter_items,
        },
    }


@router.get("/collectors")
async def collector_stats(
    since: str | None = None,
    collector_type: str | None = None,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """collector_runs 집계 — collector_type별 카운트 + 성공률/실패율.

    ``since`` 는 KST 날짜(YYYY-MM-DD)로 해석해 UTC 경계로 변환한다(타임존 갭 방지).
    """
    from signal_alpha_data_access.repositories import ObservabilityRepository

    since_utc = since_to_utc_start(since)
    async with pool.acquire() as connection:
        rows = await ObservabilityRepository(connection).collector_run_stats(
            since=since_utc,
            collector_type=collector_type,
        )

    items: list[dict[str, Any]] = []
    for row in rows:
        row = dict(row)
        stats = RunStats.from_counts(
            collected=row["collected"],
            inserted=row["inserted"],
            skipped=row["skipped"],
            failed=row["failed"],
        )
        items.append({
            "collector_type": row["collector_type"],
            "runs": int(row["runs"]),
            "runs_success": int(row["runs_success"]),
            "runs_partial": int(row["runs_partial"]),
            "runs_failed": int(row["runs_failed"]),
            "runs_running": int(row["runs_running"]),
            "last_started_at": row["last_started_at"],
            **stats.to_dict(),
        })
    return {
        "since": since,
        "since_utc": since_utc.isoformat() if since_utc else None,
        "collector_type": collector_type,
        "items": items,
    }


@router.get("/collectors/runs")
async def recent_collector_runs(
    limit: int = 20,
    collector_type: str | None = None,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """최근 collector_runs 원시 행(대시보드 표용)."""
    from signal_alpha_data_access.repositories import ObservabilityRepository

    limit = max(1, min(limit, 200))
    async with pool.acquire() as connection:
        rows = await ObservabilityRepository(connection).recent_collector_runs(
            limit=limit,
            collector_type=collector_type,
        )
    items = [dict(row) for row in rows]
    return {"count": len(items), "items": items}
