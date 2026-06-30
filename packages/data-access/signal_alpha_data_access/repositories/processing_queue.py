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
        result = await self.enqueue_with_status(
            stock_id=stock_id,
            task_type=task_type,
            priority=priority,
            source_raw_ids=source_raw_ids,
            source_signal_event_ids=source_signal_event_ids,
            source_analysis_result_ids=source_analysis_result_ids,
            task_context=task_context,
            scheduled_at=scheduled_at,
            dedupe=dedupe,
        )
        return int(result["task_id"])

    async def enqueue_with_status(
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
    ) -> dict[str, Any]:
        serialized_task_context = _jsonb(task_context)

        async def _find_active_duplicate() -> int | None:
            return await self._connection.fetchval(
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

        async def _insert() -> int:
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

        if not dedupe:
            return {"task_id": await _insert(), "reused": False}

        # 원자적 dedupe(M3): 단순 SELECT-후-INSERT 는 서로 다른 풀 커넥션에서 동시 enqueue 되는 두
        # 형제(예: PRICE+DART, 또는 드레인 데몬 + HTTP run-batch)가 모두 SELECT 를 통과해 중복 INSERT
        # 하는 레이스가 있다. 실 asyncpg 연결에서는 (task_type, stock_id) 단위 advisory xact lock 으로
        # 동시 enqueue 를 직렬화하고 SELECT+INSERT 를 한 트랜잭션으로 묶어 원자화한다(락은 트랜잭션
        # 종료 시 자동 해제 — 스키마 마이그 불필요). 단위테스트의 가짜 연결은 트랜잭션/락 의미가 없으므로
        # 비잠금 경로로 동일한 dedup 로직만 수행한다(락 래퍼는 실 DB 통합 동작이라 단위검증 대상 아님).
        if _is_real_db_connection(self._connection):
            async with self._connection.transaction():
                await self._connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1), $2)", task_type, stock_id
                )
                existing_task_id = await _find_active_duplicate()
                if existing_task_id is not None:
                    return {"task_id": existing_task_id, "reused": True}
                return {"task_id": await _insert(), "reused": False}

        existing_task_id = await _find_active_duplicate()
        if existing_task_id is not None:
            return {"task_id": existing_task_id, "reused": True}
        return {"task_id": await _insert(), "reused": False}

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
        # 지수 백오프(M1): retrying 은 즉시 재claim 되면 실패하는 외부 API 를 같은 패스에서 연속
        # 폭격한다. retry_count(증가 전 값)에 따라 30s·60s·120s… 로 미루되 30분 상한. scheduled_at
        # 절을 claim 의 `scheduled_at <= NOW()` 게이트가 읽어 백오프가 적용된다.
        await self._connection.execute(
            """
            UPDATE processing_queue
            SET
                status = $3::VARCHAR,
                retry_count = retry_count + 1,
                error_message = $2,
                finished_at = CASE WHEN $3::VARCHAR = 'failed' THEN NOW() ELSE finished_at END,
                scheduled_at = CASE
                    WHEN $3::VARCHAR = 'retrying'
                    THEN NOW() + LEAST(
                        POWER(2, retry_count) * INTERVAL '30 seconds',
                        INTERVAL '30 minutes'
                    )
                    ELSE scheduled_at
                END,
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


def _is_real_db_connection(connection: Any) -> bool:
    """advisory lock/transaction 을 적용할 실 asyncpg 연결인지 식별한다(M3).

    실 연결은 ``asyncpg.connection.Connection`` 또는 풀 프록시(``asyncpg.pool.PoolConnectionProxy``)로
    모듈 경로가 ``asyncpg.*`` 다. 단위테스트의 가짜 연결(모듈 경로 ``tests.*`` 등)은 트랜잭션/락 의미가
    없으므로 비잠금 dedup 경로를 쓴다. ``transaction``/``execute`` 보유 여부를 추가로 확인해 안전화한다.
    """
    module = type(connection).__module__ or ""
    if module.split(".", 1)[0] != "asyncpg":
        return False
    return hasattr(connection, "transaction") and hasattr(connection, "execute")


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
