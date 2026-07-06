from __future__ import annotations

from typing import Any

from signal_alpha_data_access.repositories.raw_details._common import (
    _is_undefined_column,
    _jsonb,
    logger,
)


class _HiringRawMixin:
    """Hiring 채용공고 raw detail — 정규화·OCR enrich·종목/섹터 조회·baseline·upsert."""

    _connection: Any  # _RawDetailBase 가 __init__ 에서 주입

    async def list_hiring_details_by_raw_ids(self, raw_document_ids: list[int]) -> list[Any]:
        """Hiring detail rows joined to their raw_documents, for the Normalizer.

        Mirrors ``list_dart_documents_by_raw_ids``: the normalize handler reads the
        rows the collector just enqueued (by ``source_raw_ids``) and converts each
        into a ``source_document`` + ``signal_event``.
        """
        if not raw_document_ids:
            return []

        return await self._connection.fetch(
            """
            SELECT
                raw_documents.id AS raw_document_id,
                raw_documents.stock_id,
                raw_documents.source_type,
                raw_documents.source_name,
                raw_documents.external_id,
                raw_documents.title,
                raw_documents.source_url,
                raw_documents.published_at,
                raw_documents.collected_at,
                h.keyword,
                h.job_category,
                h.job_count,
                h.previous_job_count,
                h.change_pct,
                h.extra_payload
            FROM hiring_raw_details h
            INNER JOIN raw_documents
                ON raw_documents.id = h.raw_document_id
            WHERE h.raw_document_id = ANY($1::BIGINT[])
            ORDER BY raw_documents.published_at DESC, raw_documents.id DESC
            """,
            raw_document_ids,
        )

    async def list_unenriched_hiring_details(
        self,
        *,
        limit: int = 200,
        raw_document_ids: list[int] | None = None,
    ) -> list[Any]:
        """Hiring rows still awaiting OCR enrichment (``ocr_status = 'pending'``).

        Newest raw ids first so a partial batch enriches the most recent postings.
        ``extra_payload`` carries the poster ``image_urls`` the OCR tool reads.
        See migration 028.

        ``raw_document_ids`` scopes the worklist to the rows a matching
        NORMALIZE_HIRING just promoted (queue-driven ENRICH_HIRING path); pass None
        for the global batch sweep. Already-enriched rows are skipped by the
        ``pending`` filter, so re-running a task is a no-op.

        On a stale prod schema missing ``ocr_status`` (SQLSTATE 42703) there is
        nothing to enrich, so this returns an empty list and logs a warning.
        """
        try:
            if raw_document_ids is not None:
                if not raw_document_ids:
                    return []
                return await self._connection.fetch(
                    """
                    SELECT h.raw_document_id, h.extra_payload
                    FROM hiring_raw_details h
                    WHERE h.ocr_status = 'pending'
                      AND h.raw_document_id = ANY($1::bigint[])
                    ORDER BY h.raw_document_id DESC
                    LIMIT $2
                    """,
                    raw_document_ids,
                    limit,
                )
            return await self._connection.fetch(
                """
                SELECT h.raw_document_id, h.extra_payload
                FROM hiring_raw_details h
                WHERE h.ocr_status = 'pending'
                ORDER BY h.raw_document_id DESC
                LIMIT $1
                """,
                limit,
            )
        except Exception as error:  # noqa: BLE001 — narrowed below by SQLSTATE
            if not _is_undefined_column(error):
                raise
            logger.warning(
                "hiring_raw_details lacks ocr_status (stale prod schema); "
                "no postings to enrich, returning empty batch"
            )
            return []

    async def update_hiring_ocr_skills(
        self,
        *,
        raw_document_id: int,
        skills: Any | None,
        status: str,
    ) -> None:
        """Cache OCR skill output for one posting (enriched once per posting).

        On a stale prod schema missing ``ocr_skills`` / ``ocr_status`` (SQLSTATE
        42703) this is a no-op with a logged warning — the cache simply cannot be
        written until the schema catches up.
        """
        try:
            await self._connection.execute(
                """
                UPDATE hiring_raw_details
                SET ocr_skills = $2::jsonb, ocr_status = $3
                WHERE raw_document_id = $1
                """,
                raw_document_id,
                _jsonb(skills),
                status,
            )
        except Exception as error:  # noqa: BLE001 — narrowed below by SQLSTATE
            if not _is_undefined_column(error):
                raise
            logger.warning(
                "hiring_raw_details lacks ocr_skills/ocr_status (stale prod "
                "schema); skipping OCR cache write for raw_document_id=%s",
                raw_document_id,
            )

    async def list_hiring_details_by_stock(
        self,
        *,
        stock_id: int,
        since_date: Any | None = None,
    ) -> list[Any]:
        """Hiring detail rows for one stock, newest first, within the window.

        ``hiring_raw_details`` has no own date column, so the observation date is
        taken from the joined ``raw_documents.published_at``. ``ocr_skills`` carries
        the OCR-extracted tech skills (ENRICH_HIRING, migration 028) the analyzer
        folds into the score.

        On a stale prod schema missing ``ocr_skills`` (SQLSTATE 42703) this falls
        back to a query that omits it and supplies ``ocr_skills=NULL`` so the row
        shape is unchanged for callers.
        """
        try:
            return await self._connection.fetch(
                """
                SELECT
                    h.raw_document_id,
                    h.stock_id,
                    h.keyword,
                    h.job_category,
                    h.job_count,
                    h.previous_job_count,
                    h.change_pct,
                    h.extra_payload,
                    h.ocr_skills,
                    r.title,
                    r.source_url,
                    r.published_at
                FROM hiring_raw_details h
                INNER JOIN raw_documents r ON r.id = h.raw_document_id
                WHERE h.stock_id = $1
                  AND ($2::timestamptz IS NULL OR r.published_at >= $2)
                ORDER BY r.published_at DESC, h.raw_document_id DESC
                """,
                stock_id,
                since_date,
            )
        except Exception as error:  # noqa: BLE001 — narrowed below by SQLSTATE
            if not _is_undefined_column(error):
                raise
            logger.warning(
                "hiring_raw_details lacks ocr_skills (stale prod schema); "
                "falling back without it for stock_id=%s",
                stock_id,
            )
            return await self._connection.fetch(
                """
                SELECT
                    h.raw_document_id,
                    h.stock_id,
                    h.keyword,
                    h.job_category,
                    h.job_count,
                    h.previous_job_count,
                    h.change_pct,
                    h.extra_payload,
                    NULL AS ocr_skills,
                    r.title,
                    r.source_url,
                    r.published_at
                FROM hiring_raw_details h
                INNER JOIN raw_documents r ON r.id = h.raw_document_id
                WHERE h.stock_id = $1
                  AND ($2::timestamptz IS NULL OR r.published_at >= $2)
                ORDER BY r.published_at DESC, h.raw_document_id DESC
                """,
                stock_id,
                since_date,
            )

    async def list_hiring_function_weights(self, stock_id: int) -> list[Any]:
        """Function-key → weight exposure for one stock (C4, migration 020).

        Empty when the stock is unmapped or the table is unseeded, which makes the
        analyzer fall back to own-momentum only.
        """
        return await self._connection.fetch(
            """
            SELECT f.function_key, fs.weight
            FROM hiring_job_function_stocks fs
            INNER JOIN hiring_job_functions f ON f.id = fs.job_function_id
            WHERE fs.stock_id = $1 AND f.is_active = TRUE
            """,
            stock_id,
        )

    async def list_recent_hiring_all_stocks(self, *, since_date: Any | None = None) -> list[Any]:
        """Cross-company postings within the window for sector-demand aggregation.

        Only the columns the function classifier/aggregator needs (stock_id,
        job title keyword, count, date). The loader classifies each title to a
        job function in code, so no function column is required here.
        """
        return await self._connection.fetch(
            """
            SELECT
                h.stock_id,
                h.keyword,
                h.job_count,
                r.published_at
            FROM hiring_raw_details h
            INNER JOIN raw_documents r ON r.id = h.raw_document_id
            WHERE ($1::timestamptz IS NULL OR r.published_at >= $1)
            """,
            since_date,
        )

    async def get_hiring_baseline(self, stock_id: int) -> Any | None:
        """Per-stock seasonal baseline (avg + quarterly factors), or None.

        Populated by the DataLabBaselineCollector (run_baseline.py). The hiring
        analyzer uses the quarterly factors to de-seasonalize job-count momentum;
        a missing row simply means no seasonal correction is applied.
        """
        return await self._connection.fetchrow(
            """
            SELECT
                stock_id,
                avg_search_volume,
                q1_factor,
                q2_factor,
                q3_factor,
                q4_factor,
                keyword_group_name,
                data_start_date,
                data_end_date
            FROM hiring_baseline
            WHERE stock_id = $1
            """,
            stock_id,
        )

    async def upsert_hiring_detail(
        self,
        *,
        raw_document_id: int,
        stock_id: int,
        keyword: str | None = None,
        job_category: str | None = None,
        job_count: int | None = None,
        previous_job_count: int | None = None,
        change_pct: Any | None = None,
        extra_payload: Any | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO hiring_raw_details (
                raw_document_id, stock_id, keyword, job_category, job_count,
                previous_job_count, change_pct, extra_payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (raw_document_id)
            DO UPDATE SET
                keyword = EXCLUDED.keyword,
                job_category = EXCLUDED.job_category,
                job_count = EXCLUDED.job_count,
                previous_job_count = EXCLUDED.previous_job_count,
                change_pct = EXCLUDED.change_pct,
                extra_payload = EXCLUDED.extra_payload
            RETURNING *
            """,
            raw_document_id,
            stock_id,
            keyword,
            job_category,
            job_count,
            previous_job_count,
            change_pct,
            _jsonb(extra_payload or {}),
        )
