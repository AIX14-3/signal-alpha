from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# SQLSTATE 42703 = undefined_column. The prod DB may be stale and lack the
# patent LLM columns (``llm_features`` / ``llm_status``) added in migration 019
# (now folded into 001_baseline). The three patent-LLM methods below degrade
# gracefully when those columns are absent instead of crashing the analyzer.
_UNDEFINED_COLUMN_SQLSTATE = "42703"


def _is_undefined_column(error: Exception) -> bool:
    """True when a DB error is a missing-column error (SQLSTATE 42703).

    Matches by ``sqlstate`` so it works whether the driver surfaces it as
    ``asyncpg.exceptions.UndefinedColumnError`` or any other PostgresError.
    """
    return getattr(error, "sqlstate", None) == _UNDEFINED_COLUMN_SQLSTATE


class RawDetailRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def list_dart_documents_by_raw_ids(self, raw_document_ids: list[int]) -> list[Any]:
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
                dart_raw_details.receipt_no,
                dart_raw_details.corp_code,
                dart_raw_details.report_name,
                dart_raw_details.disclosure_type,
                dart_raw_details.priority,
                dart_raw_details.priority_reason,
                dart_raw_details.is_correction,
                dart_raw_details.original_receipt_no,
                dart_raw_details.extra_payload
            FROM dart_raw_details
            INNER JOIN raw_documents
                ON raw_documents.id = dart_raw_details.raw_document_id
            WHERE dart_raw_details.raw_document_id = ANY($1::BIGINT[])
            ORDER BY raw_documents.published_at DESC, raw_documents.id DESC
            """,
            raw_document_ids,
        )

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

    async def list_report_details_by_raw_ids(self, raw_document_ids: list[int]) -> list[Any]:
        """Report detail rows joined to raw_documents, for canonical normalization."""
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
                report_raw_details.securities_firm,
                report_raw_details.publish_date,
                report_raw_details.investment_opinion,
                report_raw_details.target_price,
                report_raw_details.previous_target_price,
                report_raw_details.current_price_at_publish,
                report_raw_details.upside_pct,
                report_raw_details.key_rationale,
                report_raw_details.extracted_text
            FROM report_raw_details
            INNER JOIN raw_documents
                ON raw_documents.id = report_raw_details.raw_document_id
            WHERE report_raw_details.raw_document_id = ANY($1::BIGINT[])
              AND report_raw_details.parsing_status = 'success'
            ORDER BY report_raw_details.publish_date DESC, raw_documents.id DESC
            """,
            raw_document_ids,
        )

    async def list_report_normalize_backfill_candidates(
        self,
        *,
        stock_id: int | None = None,
        limit: int = 100,
    ) -> list[Any]:
        """Parsed report rows that have not been promoted to source_documents yet."""
        return await self._connection.fetch(
            """
            SELECT
                raw_documents.id AS raw_document_id,
                raw_documents.stock_id,
                stocks.ticker AS stock_code,
                raw_documents.title,
                raw_documents.source_url,
                raw_documents.published_at,
                report_raw_details.securities_firm,
                report_raw_details.publish_date,
                report_raw_details.parsing_status
            FROM report_raw_details
            INNER JOIN raw_documents
                ON raw_documents.id = report_raw_details.raw_document_id
            INNER JOIN stocks
                ON stocks.id = raw_documents.stock_id
            LEFT JOIN source_documents
                ON source_documents.raw_document_id = raw_documents.id
               AND source_documents.source_type = 'REPORT'
            WHERE report_raw_details.parsing_status = 'success'
              AND source_documents.id IS NULL
              AND ($1::BIGINT IS NULL OR raw_documents.stock_id = $1)
            ORDER BY
                report_raw_details.publish_date DESC NULLS LAST,
                raw_documents.published_at DESC NULLS LAST,
                raw_documents.id DESC
            LIMIT $2
            """,
            stock_id,
            limit,
        )

    async def list_stocks_for_datalab_category(self, category_id: int) -> list[Any]:
        """Active stock_ids mapped to a DataLab category (datalab_category_stocks).

        DataLab raw has no ``raw_documents``/``stock_id`` anchor, so the route
        handler resolves category → stock here to enqueue per-stock analysis.
        """
        return await self._connection.fetch(
            """
            SELECT dcs.stock_id, dcs.weight
            FROM datalab_category_stocks dcs
            INNER JOIN stocks s ON s.id = dcs.stock_id
            WHERE dcs.category_id = $1
              AND s.is_active = TRUE
            ORDER BY dcs.stock_id
            """,
            category_id,
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
                  AND ($2::date IS NULL OR p.application_date >= $2)
                ORDER BY p.application_date DESC, p.raw_document_id DESC
                """,
                stock_id,
                since_date,
            )
        except Exception as error:  # noqa: BLE001 — narrowed below by SQLSTATE
            if not _is_undefined_column(error):
                raise
            logger.warning(
                "patent_raw_details lacks llm_features/llm_status (stale prod "
                "schema); falling back without LLM columns for stock_id=%s",
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

    async def list_datalab_categories_for_stock(self, stock_id: int) -> list[Any]:
        """(category_id, weight) rows mapping this stock to its DataLab themes."""
        return await self._connection.fetch(
            """
            SELECT
                dcs.category_id,
                dcs.weight,
                dc.name AS category_name
            FROM datalab_category_stocks dcs
            INNER JOIN datalab_categories dc ON dc.id = dcs.category_id
            WHERE dcs.stock_id = $1
              AND dc.is_active = TRUE
            ORDER BY dcs.category_id
            """,
            stock_id,
        )

    async def list_dart_ownership_events_by_stock(
        self,
        *,
        stock_id: int,
        since_date: Any | None = None,
    ) -> list[Any]:
        """DART 임원·주요주주 지분변동 이벤트 행(종목 기준, 윈도우 내, 최신순).

        ``dart_ownership_events`` 는 자체 ``report_date``(=rcept_dt, known_at) 컬럼을 가져
        조인이 필요 없다. src_dart base 모델 피처(#546 Phase 1)의 PIT 게이트는 호출측
        ``assemble_features`` 가 ``report_date <= asof`` 로 강제한다.
        """
        return await self._connection.fetch(
            """
            SELECT
                stock_id,
                corp_code,
                rcept_no,
                report_date,
                holder_name,
                holder_type,
                shares,
                ratio,
                shares_delta,
                ratio_delta,
                report_reason
            FROM dart_ownership_events
            WHERE stock_id = $1
              AND ($2::date IS NULL OR report_date >= $2)
            ORDER BY report_date DESC, id DESC
            """,
            stock_id,
            since_date,
        )

    async def list_datalab_details_by_category(
        self,
        *,
        category_ids: list[int],
        since_date: Any | None = None,
    ) -> list[Any]:
        """DataLab detail rows for the given categories within the window.

        DataLab is collected by category, so detail rows carry ``category_id``
        (not stock_id) — see migration 020.
        """
        if not category_ids:
            return []

        return await self._connection.fetch(
            """
            SELECT
                d.raw_document_id,
                d.category_id,
                d.keyword,
                d.keyword_group,
                d.observed_date,
                d.search_index,
                d.previous_search_index,
                d.change_pct,
                d.period_type,
                d.device,
                d.gender,
                d.age_group,
                d.is_spike,
                d.extra_payload,
                COALESCE(dck.polarity, 'demand') AS polarity,
                COALESCE(dck.polarity_source, 'default') AS polarity_source,
                dck.polarity_model AS polarity_model
            FROM datalab_raw_details d
            LEFT JOIN datalab_category_keywords dck
                ON dck.category_id = d.category_id AND dck.keyword = d.keyword
            WHERE d.category_id = ANY($1::BIGINT[])
              AND ($2::date IS NULL OR d.observed_date >= $2)
            ORDER BY d.observed_date DESC, d.raw_document_id DESC
            """,
            category_ids,
            since_date,
        )

    async def list_datalab_details_by_raw_ids(
        self, raw_document_ids: list[int]
    ) -> list[Any]:
        """DataLab detail rows + their datalab_raw_documents meta, by raw id.

        Mirrors ``list_hiring_details_by_raw_ids``/``list_patent_details_by_raw_ids``
        for the Normalizer: the NORMALIZE_DATALAB handler reads the datalab raw the
        collector enqueued (``source_raw_ids`` = ``datalab_raw_documents.id``) and
        converts each observation into source_documents/signal_events per mapped
        stock. ``polarity`` (demand|risk) is joined from datalab_category_keywords
        so per-event direction is polarity-aware. Note the anchor table is
        ``datalab_raw_documents`` (NOT ``raw_documents``).
        """
        if not raw_document_ids:
            return []

        return await self._connection.fetch(
            """
            SELECT
                d.raw_document_id,
                d.category_id,
                d.keyword,
                d.keyword_group,
                d.observed_date,
                d.search_index,
                d.previous_search_index,
                d.change_pct,
                d.period_type,
                d.device,
                d.gender,
                d.age_group,
                d.is_spike,
                d.extra_payload,
                COALESCE(dck.polarity, 'demand') AS polarity,
                doc.source_name,
                doc.title,
                doc.source_url,
                doc.published_at,
                doc.collected_at
            FROM datalab_raw_details d
            INNER JOIN datalab_raw_documents doc
                ON doc.id = d.raw_document_id
            LEFT JOIN datalab_category_keywords dck
                ON dck.category_id = d.category_id AND dck.keyword = d.keyword
            WHERE d.raw_document_id = ANY($1::BIGINT[])
            ORDER BY d.observed_date DESC, d.raw_document_id DESC
            """,
            raw_document_ids,
        )

    async def upsert_dart_detail(
        self,
        *,
        raw_document_id: int,
        stock_id: int,
        receipt_no: str,
        report_name: str,
        extra_payload: Any,
        corp_code: str | None = None,
        disclosure_type: str | None = None,
        priority: str = "batch",
        priority_reason: str | None = None,
        is_correction: bool = False,
        original_receipt_no: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO dart_raw_details (
                raw_document_id, stock_id, receipt_no, corp_code, report_name,
                disclosure_type, priority, priority_reason, is_correction,
                original_receipt_no, extra_payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (raw_document_id)
            DO UPDATE SET
                receipt_no = EXCLUDED.receipt_no,
                corp_code = EXCLUDED.corp_code,
                report_name = EXCLUDED.report_name,
                disclosure_type = EXCLUDED.disclosure_type,
                priority = EXCLUDED.priority,
                priority_reason = EXCLUDED.priority_reason,
                is_correction = EXCLUDED.is_correction,
                original_receipt_no = EXCLUDED.original_receipt_no,
                extra_payload = EXCLUDED.extra_payload
            RETURNING *
            """,
            raw_document_id,
            stock_id,
            receipt_no,
            corp_code,
            report_name,
            disclosure_type,
            priority,
            priority_reason,
            is_correction,
            original_receipt_no,
            _jsonb(extra_payload),
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

    async def upsert_datalab_detail(
        self,
        *,
        raw_document_id: int,
        stock_id: int,
        keyword: str,
        observed_date: Any,
        search_index: Any,
        keyword_group: str | None = None,
        previous_search_index: Any | None = None,
        change_pct: Any | None = None,
        period_type: str = "daily",
        device: str = "all",
        gender: str = "all",
        age_group: str = "all",
        is_spike: bool = False,
        extra_payload: Any | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO datalab_raw_details (
                raw_document_id, stock_id, keyword, keyword_group, observed_date,
                search_index, previous_search_index, change_pct, period_type,
                device, gender, age_group, is_spike, extra_payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            ON CONFLICT (stock_id, keyword, observed_date, period_type, device, gender, age_group)
            DO UPDATE SET
                raw_document_id = EXCLUDED.raw_document_id,
                keyword_group = EXCLUDED.keyword_group,
                search_index = EXCLUDED.search_index,
                previous_search_index = EXCLUDED.previous_search_index,
                change_pct = EXCLUDED.change_pct,
                is_spike = EXCLUDED.is_spike,
                extra_payload = EXCLUDED.extra_payload
            RETURNING *
            """,
            raw_document_id,
            stock_id,
            keyword,
            keyword_group,
            observed_date,
            search_index,
            previous_search_index,
            change_pct,
            period_type,
            device,
            gender,
            age_group,
            is_spike,
            _jsonb(extra_payload),
        )


def _jsonb(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
