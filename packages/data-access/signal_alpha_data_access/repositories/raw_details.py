from __future__ import annotations

import json
from typing import Any


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
        """
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

    async def list_unenriched_patent_details(self, *, limit: int = 200) -> list[Any]:
        """Patent rows still awaiting LLM enrichment (``llm_status = 'pending'``).

        Newest filings first so a partial batch enriches the most relevant
        patents. ``extra_payload`` carries the abstract (``astrtCont``) the
        enrichment tool feeds to the LLM. See migration 019.
        """
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

    async def update_patent_llm_features(
        self,
        *,
        raw_document_id: int,
        features: Any | None,
        status: str,
    ) -> None:
        """Cache enrichment output for one patent (one-time; application_no is immutable)."""
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

    async def list_hiring_details_by_stock(
        self,
        *,
        stock_id: int,
        since_date: Any | None = None,
    ) -> list[Any]:
        """Hiring detail rows for one stock, newest first, within the window.

        ``hiring_raw_details`` has no own date column, so the observation date is
        taken from the joined ``raw_documents.published_at``.
        """
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
                COALESCE(dck.polarity, 'demand') AS polarity
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
