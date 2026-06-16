from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.database import get_database_pool

router = APIRouter(prefix="/internal/queue/dead-letter", tags=["dead-letter"])


class ReplayRequest(BaseModel):
    dead_letter_ids: list[int] = Field(min_length=1)


class ReconcileRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000)


@router.get("")
async def list_dead_letters(
    task_type: str | None = None,
    replayed: bool | None = None,
    limit: int = 50,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """종착 실패 아카이브 목록. ``replayed`` 로 재시도 여부 필터."""
    from signal_alpha_data_access.repositories import DeadLetterRepository

    limit = max(1, min(limit, 200))
    async with pool.acquire() as connection:
        rows = await DeadLetterRepository(connection).list_dead_letters(
            task_type=task_type,
            replayed=replayed,
            limit=limit,
        )
    items = [dict(row) for row in rows]
    return {"count": len(items), "items": items}


@router.post("/replay")
async def replay_dead_letters(
    request: ReplayRequest,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """선택 행을 원 task_context 그대로 processing_queue 에 재등록하고
    dead_letter 를 replayed 처리한다."""
    from signal_alpha_data_access.repositories import (
        DeadLetterRepository,
        ProcessingQueueRepository,
    )

    results: list[dict[str, Any]] = []
    async with pool.acquire() as connection:
        dead_letter_repo = DeadLetterRepository(connection)
        queue_repo = ProcessingQueueRepository(connection)

        rows = await dead_letter_repo.list_by_ids(request.dead_letter_ids)
        for row in rows:
            if row["replayed_at"] is not None:
                results.append({"dead_letter_id": row["id"], "skipped": "already replayed"})
                continue
            new_task_id = await queue_repo.enqueue(
                stock_id=row["stock_id"],
                task_type=row["task_type"],
                priority=row["priority"],
                source_raw_ids=row["source_raw_ids"],
                source_signal_event_ids=row["source_signal_event_ids"],
                source_analysis_result_ids=row["source_analysis_result_ids"],
                task_context=row["task_context"],
            )
            await dead_letter_repo.mark_replayed(
                dead_letter_id=row["id"],
                replayed_task_id=new_task_id,
            )
            results.append({"dead_letter_id": row["id"], "replayed_task_id": new_task_id})

    replayed = [r for r in results if "replayed_task_id" in r]
    return {"replayed_count": len(replayed), "results": results}


@router.post("/reconcile")
async def reconcile_dead_letters(
    request: ReconcileRequest,
    pool: Any = Depends(get_database_pool),
) -> dict[str, int]:
    """백스탑: sweep_stale 등으로 생긴 미아카이브 failed 태스크를 일괄 아카이브."""
    from signal_alpha_data_access.repositories import DeadLetterRepository

    async with pool.acquire() as connection:
        archived = await DeadLetterRepository(connection).reconcile_failed(limit=request.limit)
    return {"archived_count": archived}
