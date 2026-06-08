from __future__ import annotations

import json
from typing import Any


class ProcessingQueueRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def enqueue(
        self,
        *,
        stock_id: int,
        task_type: str,
        priority: str = "batch",
        source_raw_ids: list[int] | None = None,
        source_signal_event_ids: list[int] | None = None,
        source_analysis_result_ids: list[int] | None = None,
        task_context: Any | None = None,
        scheduled_at: Any | None = None,
    ) -> int:
        return await self._connection.fetchval(
            """
            INSERT INTO processing_queue (
                stock_id,
                task_type,
                priority,
                source_raw_ids,
                source_signal_event_ids,
                source_analysis_result_ids,
                task_context,
                scheduled_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, COALESCE($8, NOW()))
            RETURNING id
            """,
            stock_id,
            task_type,
            priority,
            source_raw_ids,
            source_signal_event_ids,
            source_analysis_result_ids,
            _jsonb(task_context),
            scheduled_at,
        )

    async def claim_next_pending(self, *, task_type: str) -> Any:
        return await self._connection.fetchrow(
            """
            WITH next_task AS (
                SELECT id
                FROM processing_queue
                WHERE task_type = $1
                  AND status IN ('pending', 'retrying')
                  AND scheduled_at <= NOW()
                ORDER BY
                    CASE priority WHEN 'immediate' THEN 0 ELSE 1 END,
                    scheduled_at ASC,
                    id ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE processing_queue
            SET
                status = 'running',
                started_at = NOW(),
                updated_at = NOW()
            WHERE id = (SELECT id FROM next_task)
            RETURNING *
            """,
            task_type,
        )

    async def mark_success(self, *, task_id: int) -> None:
        await self._connection.execute(
            """
            UPDATE processing_queue
            SET
                status = 'success',
                finished_at = NOW(),
                updated_at = NOW()
            WHERE id = $1
            """,
            task_id,
        )

    async def mark_failed(
        self,
        *,
        task_id: int,
        error_message: str,
        retry: bool = False,
    ) -> None:
        status = "retrying" if retry else "failed"
        await self._connection.execute(
            """
            UPDATE processing_queue
            SET
                status = $3::VARCHAR,
                retry_count = retry_count + 1,
                error_message = $2,
                finished_at = CASE WHEN $3::VARCHAR = 'failed' THEN NOW() ELSE finished_at END,
                scheduled_at = CASE WHEN $3::VARCHAR = 'retrying' THEN NOW() ELSE scheduled_at END,
                updated_at = NOW()
            WHERE id = $1
            """,
            task_id,
            error_message,
            status,
        )

    async def mark_skipped(self, *, task_id: int, message: str | None = None) -> None:
        await self._connection.execute(
            """
            UPDATE processing_queue
            SET
                status = 'skipped',
                error_message = $2,
                finished_at = NOW(),
                updated_at = NOW()
            WHERE id = $1
            """,
            task_id,
            message,
        )


def _jsonb(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
