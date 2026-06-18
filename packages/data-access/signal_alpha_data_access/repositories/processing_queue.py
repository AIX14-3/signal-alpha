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
        dedupe: bool = False,
    ) -> int:
        serialized_task_context = _jsonb(task_context)
        if dedupe:
            existing_task_id = await self._connection.fetchval(
                """
                SELECT id
                FROM processing_queue
                WHERE stock_id = $1
                  AND task_type = $2
                  AND task_context IS NOT DISTINCT FROM $3::JSONB
                  AND source_raw_ids IS NOT DISTINCT FROM $4::BIGINT[]
                  AND source_signal_event_ids IS NOT DISTINCT FROM $5::BIGINT[]
                  AND source_analysis_result_ids IS NOT DISTINCT FROM $6::BIGINT[]
                  AND status IN ('pending', 'running', 'retrying')
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                stock_id,
                task_type,
                serialized_task_context,
                source_raw_ids,
                source_signal_event_ids,
                source_analysis_result_ids,
            )
            if existing_task_id is not None:
                return existing_task_id

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
            serialized_task_context,
            scheduled_at,
        )

    async def has_open_or_successful_task(
        self,
        *,
        raw_document_id: int,
        task_type: str,
    ) -> bool:
        """Whether a raw_document already has a normalization task worth keeping.

        F1 안전망 판단용. 활성(pending/running/retrying) 또는 성공(success) task 가
        하나라도 있으면 True. failed/skipped 뿐이거나 task 자체가 없으면 False →
        호출부(collector)가 재수집 시 재인큐(복구)한다. 'success' 를 포함시켜 이미
        정규화된 raw 를 매 재수집마다 다시 처리하는 것을 막는다.
        """
        existing = await self._connection.fetchval(
            """
            SELECT 1
            FROM processing_queue
            WHERE task_type = $1
              AND $2 = ANY(source_raw_ids)
              AND status IN ('pending', 'running', 'retrying', 'success')
            LIMIT 1
            """,
            task_type,
            raw_document_id,
        )
        return existing is not None

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

    async def claim_pending_by_id(self, *, task_id: int, task_type: str) -> Any:
        return await self._connection.fetchrow(
            """
            UPDATE processing_queue
            SET
                status = 'running',
                started_at = NOW(),
                updated_at = NOW()
            WHERE id = $1
              AND task_type = $2
              AND status IN ('pending', 'retrying')
              AND scheduled_at <= NOW()
            RETURNING *
            """,
            task_id,
            task_type,
        )

    async def list_tasks(
        self,
        *,
        stock_code: str | None = None,
        task_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                processing_queue.*,
                stocks.ticker AS stock_code,
                stocks.name AS stock_name
            FROM processing_queue
            INNER JOIN stocks ON stocks.id = processing_queue.stock_id
            WHERE ($1::VARCHAR IS NULL OR stocks.ticker = $1)
              AND ($2::VARCHAR IS NULL OR processing_queue.task_type = $2)
              AND ($3::VARCHAR IS NULL OR processing_queue.status = $3)
            ORDER BY processing_queue.created_at DESC, processing_queue.id DESC
            LIMIT $4
            """,
            stock_code,
            task_type,
            status,
            limit,
        )

    async def retry_task(self, *, task_id: int) -> Any:
        return await self._connection.fetchrow(
            """
            UPDATE processing_queue
            SET
                status = 'retrying',
                error_message = NULL,
                started_at = NULL,
                finished_at = NULL,
                scheduled_at = NOW(),
                updated_at = NOW()
            WHERE id = $1
              AND status IN ('failed', 'skipped', 'retrying', 'pending')
            RETURNING *
            """,
            task_id,
        )

    async def mark_success(self, *, task_id: int) -> None:
        await self._connection.execute(
            """
            UPDATE processing_queue
            SET
                status = 'success',
                error_message = NULL,
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

    async def sweep_stale_active_tasks(
        self,
        *,
        running_timeout_minutes: int = 30,
        retrying_timeout_minutes: int = 120,
    ) -> dict[str, int]:
        retried_status = await self._connection.execute(
            """
            UPDATE processing_queue
            SET
                status = 'retrying',
                retry_count = retry_count + 1,
                error_message = COALESCE(error_message, $2),
                started_at = NULL,
                scheduled_at = NOW(),
                updated_at = NOW()
            WHERE status = 'running'
              AND started_at < NOW() - ($1::INT * INTERVAL '1 minute')
              AND retry_count < max_retry_count
            """,
            running_timeout_minutes,
            "Task exceeded running timeout.",
        )
        failed_status = await self._connection.execute(
            """
            UPDATE processing_queue
            SET
                status = 'failed',
                error_message = COALESCE(error_message, $3),
                finished_at = NOW(),
                updated_at = NOW()
            WHERE (
                    status = 'running'
                    AND started_at < NOW() - ($1::INT * INTERVAL '1 minute')
                    AND retry_count >= max_retry_count
                )
               OR (
                    status = 'retrying'
                    AND scheduled_at < NOW() - ($2::INT * INTERVAL '1 minute')
                    AND retry_count >= max_retry_count
                )
            """,
            running_timeout_minutes,
            retrying_timeout_minutes,
            "Task exceeded retry timeout.",
        )
        return {
            "retried_count": _command_row_count(retried_status),
            "failed_count": _command_row_count(failed_status),
        }


def _jsonb(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _command_row_count(command_status: str) -> int:
    parts = command_status.split()
    if not parts:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0
