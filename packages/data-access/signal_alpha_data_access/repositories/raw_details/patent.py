from __future__ import annotations

from typing import Any

from signal_alpha_data_access.repositories.raw_details._common import (
    _is_undefined_column,
    _jsonb,
    logger,
)


class _PatentRawMixin:
    """Patent 특허 raw detail — 정규화·종목 조회·LLM enrich·upsert."""

    _connection: Any  # _RawDetailBase 가 __init__ 에서 주입

    async def list_patent_details_by_raw_ids(self, raw_document_ids: list[int]) -> list[Any]:
        """Patent detail rows joined to their raw_documents, for the Normalizer."""
        if not raw_document_ids:
            return []

        return await self._connection.fetch(
            """
            SELECT
                raw_documents.id AS raw_document_id,
                raw_documents.stock_id,
                raw_documents.source_type,
                raw_documents.source_name,
                raw_documents.title,
                raw_documents.source_url,
                raw_documents.published_at,
                raw_documents.collected_at,
                p.application_no,
                p.patent_title,
                p.applicant_name,
                p.application_date,
                p.tech_category,
                p.is_new_category,
                p.extra_payload
            FROM patent_raw_details p
            INNER JOIN raw_documents
                ON raw_documents.id = p.raw_document_id
            WHERE p.raw_document_id = ANY($1::BIGINT[])
            ORDER BY raw_documents.published_at DESC, raw_documents.id DESC
            """,
            raw_document_ids,
        )

    async def list_patent_details_by_stock(
        self,
        *,
        stock_id: int,
        since_date: Any | None = None,
    ) -> list[Any]:
        """Patent detail rows for one stock, newest first, within the window.

        ``since_date`` (a date) bounds the lookback; pass None for all history.

        On a stale prod schema missing the ``llm_features`` / ``llm_status``
        columns (SQLSTATE 42703) this falls back to a query that omits them and
        supplies ``llm_features=NULL`` / ``llm_status=NULL`` placeholders so the
        row shape is unchanged for callers.
        """
        try:
            return await self._connection.fetch(
                """
                SELECT
                    p.raw_document_id,
                    p.stock_id,
                    p.application_no,
                    p.patent_title,
                    p.applicant_name,
                    p.application_date,
                    p.publication_date,
                    p.tech_category,
                    p.is_new_category,
                    p.extra_payload,
                    p.llm_features,
                    p.llm_status,
                    r.title,
                    r.source_url,
                    r.published_at
                FROM patent_raw_details p
                INNER JOIN raw_documents r ON r.id = p.raw_document_id
                WHERE p.stock_id = $1
                  AND ($2::date IS NULL
                       OR COALESCE(p.publication_date, p.application_date) >= $2)
                ORDER BY COALESCE(p.publication_date, p.application_date) DESC,
                         p.raw_document_id DESC
                """,
                stock_id,
                since_date,
            )
        except Exception as error:  # noqa: BLE001 — narrowed below by SQLSTATE
            if not _is_undefined_column(error):
                raise
            logger.warning(
                "patent_raw_details lacks llm_features/llm_status/publication_date "
                "(stale prod schema); falling back with NULL placeholders for "
                "stock_id=%s",
                stock_id,
            )
            return await self._connection.fetch(
                """
                SELECT
                    p.raw_document_id,
                    p.stock_id,
                    p.application_no,
                    p.patent_title,
                    p.applicant_name,
                    p.application_date,
                    NULL::date AS publication_date,
                    p.tech_category,
                    p.is_new_category,
                    p.extra_payload,
                    NULL AS llm_features,
                    NULL AS llm_status,
                    r.title,
                    r.source_url,
                    r.published_at
                FROM patent_raw_details p
                INNER JOIN raw_documents r ON r.id = p.raw_document_id
                WHERE p.stock_id = $1
                  AND ($2::date IS NULL OR p.application_date >= $2)
                ORDER BY p.application_date DESC, p.raw_document_id DESC
                """,
                stock_id,
                since_date,
            )

    async def patent_filing_trend_by_stock(
        self,
        *,
        stock_id: int,
    ) -> list[Any]:
        """연도별 특허 **출원(filing)** 건수 — 장기 R&D 추이용.

        최근성 창(공개일 기준)과 무관하게 종목의 전체 이력을 출원 연도로 집계한다.
        "최근 공개된 특허"(신호)와 "장기 출원 추이"(맥락)는 서로 다른 날짜 축이므로
        분리한다: 신호는 publication_date, 추이는 application_date. 오래된→최신 순.
        """
        return await self._connection.fetch(
            """
            SELECT
                EXTRACT(YEAR FROM application_date)::int AS year,
                COUNT(*)::int AS count
            FROM patent_raw_details
            WHERE stock_id = $1
            GROUP BY year
            ORDER BY year
            """,
            stock_id,
        )

    async def list_unenriched_patent_details(
        self,
        *,
        limit: int = 200,
        raw_document_ids: list[int] | None = None,
    ) -> list[Any]:
        """Patent rows still awaiting LLM enrichment (``llm_status = 'pending'``).

        Newest filings first so a partial batch enriches the most relevant
        patents. ``extra_payload`` carries the abstract (``astrtCont``) the
        enrichment tool feeds to the LLM. See migration 019.

        ``raw_document_ids`` scopes the worklist to a specific set of patents
        (the queue-driven ENRICH_PATENT path enriches only the rows a matching
        NORMALIZE_PATENT just promoted); pass None for the global batch sweep.
        Already-enriched rows in the set are skipped by the ``pending`` filter,
        so re-running a task is a no-op.

        On a stale prod schema missing ``llm_status`` (SQLSTATE 42703) there is
        nothing to enrich, so this returns an empty list and logs a warning.
        """
        try:
            if raw_document_ids is not None:
                if not raw_document_ids:
                    return []
                return await self._connection.fetch(
                    """
                    SELECT
                        p.raw_document_id,
                        p.application_no,
                        p.patent_title,
                        p.extra_payload
                    FROM patent_raw_details p
                    WHERE p.llm_status = 'pending'
                      AND p.raw_document_id = ANY($1::bigint[])
                    ORDER BY p.application_date DESC, p.raw_document_id DESC
                    LIMIT $2
                    """,
                    raw_document_ids,
                    limit,
                )
            return await self._connection.fetch(
                """
                SELECT
                    p.raw_document_id,
                    p.application_no,
                    p.patent_title,
                    p.extra_payload
                FROM patent_raw_details p
                WHERE p.llm_status = 'pending'
                ORDER BY p.application_date DESC, p.raw_document_id DESC
                LIMIT $1
                """,
                limit,
            )
        except Exception as error:  # noqa: BLE001 — narrowed below by SQLSTATE
            if not _is_undefined_column(error):
                raise
            logger.warning(
                "patent_raw_details lacks llm_status (stale prod schema); "
                "no patents to enrich, returning empty batch"
            )
            return []

    async def update_patent_llm_features(
        self,
        *,
        raw_document_id: int,
        features: Any | None,
        status: str,
    ) -> None:
        """Cache enrichment output for one patent (one-time; application_no is immutable).

        On a stale prod schema missing ``llm_features`` / ``llm_status``
        (SQLSTATE 42703) this is a no-op with a logged warning — the cache
        simply cannot be written until the schema catches up.
        """
        try:
            await self._connection.execute(
                """
                UPDATE patent_raw_details
                SET llm_features = $2::jsonb, llm_status = $3
                WHERE raw_document_id = $1
                """,
                raw_document_id,
                _jsonb(features),
                status,
            )
        except Exception as error:  # noqa: BLE001 — narrowed below by SQLSTATE
            if not _is_undefined_column(error):
                raise
            logger.warning(
                "patent_raw_details lacks llm_features/llm_status (stale prod "
                "schema); skipping LLM cache write for raw_document_id=%s",
                raw_document_id,
            )

    async def upsert_patent_detail(
        self,
        *,
        raw_document_id: int,
        stock_id: int,
        application_no: str,
        patent_title: str,
        application_date: Any,
        extra_payload: Any,
        applicant_name: str | None = None,
        tech_category: str | None = None,
        is_new_category: bool = False,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO patent_raw_details (
                raw_document_id, stock_id, application_no, patent_title,
                applicant_name, application_date, tech_category, is_new_category,
                extra_payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (application_no)
            DO UPDATE SET
                raw_document_id = EXCLUDED.raw_document_id,
                stock_id = EXCLUDED.stock_id,
                patent_title = EXCLUDED.patent_title,
                applicant_name = EXCLUDED.applicant_name,
                application_date = EXCLUDED.application_date,
                tech_category = EXCLUDED.tech_category,
                is_new_category = EXCLUDED.is_new_category,
                extra_payload = EXCLUDED.extra_payload
            RETURNING *
            """,
            raw_document_id,
            stock_id,
            application_no,
            patent_title,
            applicant_name,
            application_date,
            tech_category,
            is_new_category,
            _jsonb(extra_payload),
        )
