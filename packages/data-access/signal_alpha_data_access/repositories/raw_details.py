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
