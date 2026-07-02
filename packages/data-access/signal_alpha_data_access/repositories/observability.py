from __future__ import annotations

from typing import Any


class ObservabilityRepository:
    """Read-only aggregates over collector_runs / processing_queue for the
    pipeline visibility endpoints. No writes — surfaces data already recorded."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def queue_stats(self) -> list[Any]:
        """processing_queue counts grouped by (task_type, status), with the
        oldest still-waiting (pending/retrying) scheduled_at per task_type.

        Served by idx_queue_type_status (task_type, status) — see migration 003.
        """
        return await self._connection.fetch(
            """
            SELECT
                task_type,
                status,
                COUNT(*) AS count,
                MIN(scheduled_at) FILTER (
                    WHERE status IN ('pending', 'retrying')
                ) AS oldest_waiting_at
            FROM processing_queue
            GROUP BY task_type, status
            ORDER BY task_type, status
            """
        )

    async def recent_failed_count(self, *, window_minutes: int) -> int:
        """최근 윈도우 안에서 failed 로 남아 있는 processing_queue 행 수.

        failed 는 종착 상태(자동 정리 없음)라 총계는 평생 누적 — 스케줄러 backpressure 가
        총계를 쓰면 한 번 임계를 넘은 뒤 모든 수집이 영구 홀드된다. updated_at 은 touch
        트리거로 상태 전이 시 갱신되므로 "최근 실패" 판정 기준으로 쓴다.
        """
        value = await self._connection.fetchval(
            """
            SELECT COUNT(*)
            FROM processing_queue
            WHERE status = 'failed'
              AND updated_at >= NOW() - make_interval(mins => $1)
            """,
            window_minutes,
        )
        return int(value or 0)

    async def collector_run_stats(
        self,
        *,
        since: Any | None = None,
        collector_type: str | None = None,
    ) -> list[Any]:
        """Per collector_type run aggregates within the window.

        ``since`` MUST be an aware/UTC datetime (or None) — the caller converts a
        KST day boundary via app.observability.since_to_utc_start so the
        TIMESTAMPTZ comparison is unambiguous. Rates are computed in the service
        layer from these sums (query returns raw totals only).
        """
        return await self._connection.fetch(
            """
            SELECT
                collector_type,
                COUNT(*) AS runs,
                COUNT(*) FILTER (WHERE status = 'success') AS runs_success,
                COUNT(*) FILTER (WHERE status = 'partial') AS runs_partial,
                COUNT(*) FILTER (WHERE status = 'failed') AS runs_failed,
                COUNT(*) FILTER (WHERE status = 'running') AS runs_running,
                COALESCE(SUM(collected_count), 0) AS collected,
                COALESCE(SUM(inserted_count), 0) AS inserted,
                COALESCE(SUM(skipped_count), 0) AS skipped,
                COALESCE(SUM(failed_count), 0) AS failed,
                MAX(started_at) AS last_started_at
            FROM collector_runs
            WHERE ($1::timestamptz IS NULL OR started_at >= $1)
              AND ($2::varchar IS NULL OR collector_type = $2)
            GROUP BY collector_type
            ORDER BY collector_type
            """,
            since,
            collector_type,
        )

    async def recent_collector_runs(
        self,
        *,
        limit: int = 20,
        collector_type: str | None = None,
    ) -> list[Any]:
        """Most recent collector_runs rows (for a dashboard table)."""
        return await self._connection.fetch(
            """
            SELECT
                id,
                collector_type,
                run_mode,
                status,
                started_at,
                finished_at,
                collected_count,
                inserted_count,
                skipped_count,
                failed_count,
                error_message
            FROM collector_runs
            WHERE ($2::varchar IS NULL OR collector_type = $2)
            ORDER BY started_at DESC, id DESC
            LIMIT $1
            """,
            limit,
            collector_type,
        )
