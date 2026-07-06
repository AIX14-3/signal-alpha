from __future__ import annotations

from typing import Any


class _ReportRawMixin:
    """증권사 리포트 raw detail — 정규화용 조회·backfill 후보 조회."""

    _connection: Any  # _RawDetailBase 가 __init__ 에서 주입

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
