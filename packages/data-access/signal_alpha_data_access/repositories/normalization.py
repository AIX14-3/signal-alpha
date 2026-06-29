from __future__ import annotations

from typing import Any


class NormalizationRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_source_document(
        self,
        *,
        raw_document_id: int,
        stock_id: int,
        source_type: str,
        source_name: str,
        title: str,
        source_url: str | None,
        published_at: Any,
        collected_at: Any,
        reliability_level: str = "medium",
        is_official: bool = False,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO source_documents (
                raw_document_id,
                stock_id,
                source_type,
                source_name,
                title,
                source_url,
                published_at,
                collected_at,
                reliability_level,
                is_official
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (raw_document_id)
            DO UPDATE SET
                source_name = EXCLUDED.source_name,
                title = EXCLUDED.title,
                source_url = EXCLUDED.source_url,
                published_at = EXCLUDED.published_at,
                collected_at = EXCLUDED.collected_at,
                reliability_level = EXCLUDED.reliability_level,
                is_official = EXCLUDED.is_official
            RETURNING *
            """,
            raw_document_id,
            stock_id,
            source_type,
            source_name,
            title,
            source_url,
            published_at,
            collected_at,
            reliability_level,
            is_official,
        )

    async def upsert_external_source_document(
        self,
        *,
        external_ref_type: str,
        external_ref_id: int,
        stock_id: int,
        source_type: str,
        source_name: str,
        title: str,
        source_url: str | None,
        published_at: Any,
        collected_at: Any,
        reliability_level: str = "medium",
        is_official: bool = False,
    ) -> Any:
        """Upsert a source_document anchored to raw that lives outside raw_documents.

        For sources whose raw is not in ``raw_documents`` (DataLab now; future
        non-raw_documents sources later). ``raw_document_id`` stays NULL and the
        anchor is the generic ``(external_ref_type, external_ref_id)`` pair — see
        ``004_datalab_source_anchor.sql``. One observation fans out to N stocks,
        so the conflict key is ``(external_ref_type, external_ref_id, stock_id)``
        (the ``uq_source_doc_external`` partial unique index) for rerun idempotency.
        """
        return await self._connection.fetchrow(
            """
            INSERT INTO source_documents (
                external_ref_type,
                external_ref_id,
                stock_id,
                source_type,
                source_name,
                title,
                source_url,
                published_at,
                collected_at,
                reliability_level,
                is_official
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (external_ref_type, external_ref_id, stock_id)
                WHERE external_ref_type IS NOT NULL
            DO UPDATE SET
                source_name = EXCLUDED.source_name,
                title = EXCLUDED.title,
                source_url = EXCLUDED.source_url,
                published_at = EXCLUDED.published_at,
                collected_at = EXCLUDED.collected_at,
                reliability_level = EXCLUDED.reliability_level,
                is_official = EXCLUDED.is_official
            RETURNING *
            """,
            external_ref_type,
            external_ref_id,
            stock_id,
            source_type,
            source_name,
            title,
            source_url,
            published_at,
            collected_at,
            reliability_level,
            is_official,
        )

    async def upsert_signal_event(
        self,
        *,
        stock_id: int,
        source_document_id: int,
        event_hash: str,
        source_type: str,
        event_type: str,
        event_date: Any,
        signal_direction: str,
        impact_level: str,
        title: str,
        summary: str | None = None,
        evidence_text: str | None = None,
        evidence_url: str | None = None,
        needs_review: bool = False,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO signal_events (
                stock_id,
                source_document_id,
                event_hash,
                source_type,
                event_type,
                event_date,
                signal_direction,
                impact_level,
                title,
                summary,
                evidence_text,
                evidence_url,
                needs_review
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (event_hash)
            DO UPDATE SET
                source_document_id = EXCLUDED.source_document_id,
                event_type = EXCLUDED.event_type,
                event_date = EXCLUDED.event_date,
                signal_direction = EXCLUDED.signal_direction,
                impact_level = EXCLUDED.impact_level,
                title = EXCLUDED.title,
                summary = EXCLUDED.summary,
                evidence_text = EXCLUDED.evidence_text,
                evidence_url = EXCLUDED.evidence_url,
                needs_review = EXCLUDED.needs_review
            RETURNING *
            """,
            stock_id,
            source_document_id,
            event_hash,
            source_type,
            event_type,
            event_date,
            signal_direction,
            impact_level,
            title,
            summary,
            evidence_text,
            evidence_url,
            needs_review,
        )

    async def upsert_signal_metric(
        self,
        *,
        signal_event_id: int,
        metric_name: str,
        metric_value: Any,
        metric_unit: str | None = None,
        previous_value: Any | None = None,
        change_pct: Any | None = None,
        period_start: Any | None = None,
        period_end: Any | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO signal_metrics (
                signal_event_id,
                metric_name,
                metric_value,
                metric_unit,
                previous_value,
                change_pct,
                period_start,
                period_end
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (signal_event_id, metric_name)
            DO UPDATE SET
                metric_value = EXCLUDED.metric_value,
                metric_unit = EXCLUDED.metric_unit,
                previous_value = EXCLUDED.previous_value,
                change_pct = EXCLUDED.change_pct,
                period_start = EXCLUDED.period_start,
                period_end = EXCLUDED.period_end
            RETURNING *
            """,
            signal_event_id,
            metric_name,
            metric_value,
            metric_unit,
            previous_value,
            change_pct,
            period_start,
            period_end,
        )

    async def list_signal_events_by_ids(self, signal_event_ids: list[int]) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                signal_events.*,
                source_documents.source_name,
                source_documents.source_url,
                source_documents.published_at,
                source_documents.reliability_level,
                source_documents.is_official
            FROM signal_events
            INNER JOIN source_documents
                ON source_documents.id = signal_events.source_document_id
            WHERE signal_events.id = ANY($1::BIGINT[])
            ORDER BY
                CASE signal_events.impact_level
                    WHEN 'high' THEN 0
                    WHEN 'medium' THEN 1
                    ELSE 2
                END,
                signal_events.event_date DESC,
                signal_events.id ASC
            """,
            signal_event_ids,
        )

    async def list_recent_source_events(
        self,
        *,
        stock_id: int,
        source_type: str,
        as_of: Any = None,
        limit: int = 20,
    ) -> list[Any]:
        """종목+소스의 최근 이벤트 — 임팩트(high>medium>low) 우선, 최신순, 상한 limit.

        LLM 서술 입력용. ``as_of`` 지정 시 그 날짜 이하만(PIT 안전). 미지정 시 전체 최신.
        """
        return await self._connection.fetch(
            """
            SELECT
                signal_events.*,
                source_documents.source_name,
                source_documents.source_url,
                source_documents.published_at,
                source_documents.reliability_level,
                source_documents.is_official
            FROM signal_events
            INNER JOIN source_documents
                ON source_documents.id = signal_events.source_document_id
            WHERE signal_events.stock_id = $1
              AND signal_events.source_type = $2
              AND ($3::DATE IS NULL OR signal_events.event_date <= $3::DATE)
            ORDER BY
                CASE signal_events.impact_level
                    WHEN 'high' THEN 0
                    WHEN 'medium' THEN 1
                    ELSE 2
                END,
                signal_events.event_date DESC,
                signal_events.id DESC
            LIMIT $4
            """,
            stock_id,
            source_type,
            as_of,
            int(limit),
        )

    async def list_signal_events_for_stock_date(
        self,
        *,
        stock_id: int,
        source_type: str,
        event_date: Any,
    ) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                signal_events.*,
                source_documents.source_name,
                source_documents.source_url,
                source_documents.published_at,
                source_documents.reliability_level,
                source_documents.is_official
            FROM signal_events
            INNER JOIN source_documents
                ON source_documents.id = signal_events.source_document_id
            WHERE signal_events.stock_id = $1
              AND signal_events.source_type = $2
              AND signal_events.event_date = $3::DATE
            ORDER BY
                CASE signal_events.impact_level
                    WHEN 'high' THEN 0
                    WHEN 'medium' THEN 1
                    ELSE 2
                END,
                signal_events.id ASC
            """,
            stock_id,
            source_type,
            event_date,
        )

    async def record_validation_log(
        self,
        *,
        target_type: str,
        validation_type: str,
        passed: bool,
        target_id_int: int | None = None,
        target_id_uuid: Any | None = None,
        message: str | None = None,
        retry_count: int = 0,
    ) -> int:
        if (target_id_int is None and target_id_uuid is None) or (
            target_id_int is not None and target_id_uuid is not None
        ):
            raise ValueError("Exactly one target identifier is required.")

        return await self._connection.fetchval(
            """
            INSERT INTO validation_logs (
                target_type,
                target_id_int,
                target_id_uuid,
                validation_type,
                passed,
                message,
                retry_count
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            target_type,
            target_id_int,
            target_id_uuid,
            validation_type,
            passed,
            message,
            retry_count,
        )
