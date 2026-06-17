from __future__ import annotations

import json
from typing import Any


class DeadLetterRepository:
    """Dead Letter Queue — archive of terminally failed processing_queue tasks
    plus the replay ledger. See migration 005_dead_letter.sql.

    Archival is idempotent on processing_queue_id, so the runner hook (immediate)
    and reconcile backstop (sweep-produced failures) can both INSERT safely.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def archive_failed_task(
        self,
        *,
        processing_queue_id: int,
        stock_id: int | None,
        task_type: str,
        priority: str,
        source_raw_ids: list[int] | None,
        source_signal_event_ids: list[int] | None,
        source_analysis_result_ids: list[int] | None,
        task_context: Any | None,
        final_error_message: str | None,
        final_retry_count: int,
    ) -> Any:
        """Archive one terminally failed task. Idempotent: a second call for the
        same processing_queue_id is a no-op (returns None)."""
        return await self._connection.fetchrow(
            """
            INSERT INTO dead_letter (
                processing_queue_id,
                stock_id,
                task_type,
                priority,
                source_raw_ids,
                source_signal_event_ids,
                source_analysis_result_ids,
                task_context,
                final_error_message,
                final_retry_count
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::JSONB, $9, $10)
            ON CONFLICT (processing_queue_id) DO NOTHING
            RETURNING *
            """,
            processing_queue_id,
            stock_id,
            task_type,
            priority,
            source_raw_ids,
            source_signal_event_ids,
            source_analysis_result_ids,
            _jsonb(task_context),
            final_error_message,
            final_retry_count,
        )

    async def reconcile_failed(self, *, limit: int = 100) -> int:
        """Backstop: archive processing_queue rows that are terminally 'failed'
        but not yet in dead_letter (e.g. produced by sweep_stale_active_tasks).
        Returns the number of rows archived."""
        status = await self._connection.execute(
            """
            INSERT INTO dead_letter (
                processing_queue_id,
                stock_id,
                task_type,
                priority,
                source_raw_ids,
                source_signal_event_ids,
                source_analysis_result_ids,
                task_context,
                final_error_message,
                final_retry_count
            )
            SELECT
                pq.id,
                pq.stock_id,
                pq.task_type,
                pq.priority,
                pq.source_raw_ids,
                pq.source_signal_event_ids,
                pq.source_analysis_result_ids,
                pq.task_context,
                pq.error_message,
                pq.retry_count
            FROM processing_queue pq
            WHERE pq.status = 'failed'
              AND NOT EXISTS (
                  SELECT 1 FROM dead_letter dl
                  WHERE dl.processing_queue_id = pq.id
              )
            ORDER BY pq.finished_at ASC NULLS LAST, pq.id ASC
            LIMIT $1
            ON CONFLICT (processing_queue_id) DO NOTHING
            """,
            limit,
        )
        return _command_row_count(status)

    async def list_dead_letters(
        self,
        *,
        task_type: str | None = None,
        replayed: bool | None = None,
        limit: int = 50,
    ) -> list[Any]:
        """List archived failures. ``replayed`` True/False filters on
        replayed_at IS [NOT] NULL; None returns both."""
        return await self._connection.fetch(
            """
            SELECT *
            FROM dead_letter
            WHERE ($1::VARCHAR IS NULL OR task_type = $1)
              AND (
                  $2::BOOLEAN IS NULL
                  OR ($2 = TRUE AND replayed_at IS NOT NULL)
                  OR ($2 = FALSE AND replayed_at IS NULL)
              )
            ORDER BY archived_at DESC, id DESC
            LIMIT $3
            """,
            task_type,
            replayed,
            limit,
        )

    async def list_by_ids(self, ids: list[int]) -> list[Any]:
        """Fetch dead_letter rows by id (replay targets)."""
        if not ids:
            return []
        return await self._connection.fetch(
            """
            SELECT *
            FROM dead_letter
            WHERE id = ANY($1::BIGINT[])
            ORDER BY id ASC
            """,
            ids,
        )

    async def mark_replayed(
        self,
        *,
        dead_letter_id: int,
        replayed_task_id: int,
    ) -> Any:
        """Record that a dead_letter row was re-enqueued as replayed_task_id."""
        return await self._connection.fetchrow(
            """
            UPDATE dead_letter
            SET
                replayed_at = NOW(),
                replayed_task_id = $2
            WHERE id = $1
            RETURNING *
            """,
            dead_letter_id,
            replayed_task_id,
        )

    async def dead_letter_stats(self) -> list[Any]:
        """Counts grouped by task_type: total and not-yet-replayed.
        Surfaced via /internal/stats/queue."""
        return await self._connection.fetch(
            """
            SELECT
                task_type,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE replayed_at IS NULL) AS unreplayed
            FROM dead_letter
            GROUP BY task_type
            ORDER BY task_type
            """
        )


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
