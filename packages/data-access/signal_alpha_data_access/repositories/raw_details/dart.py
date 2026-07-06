from __future__ import annotations

from typing import Any

from signal_alpha_data_access.repositories.raw_details._common import _jsonb


class _DartRawMixin:
    """DART 공시 raw detail — 정규화용 조회·지분변동 이벤트·upsert."""

    _connection: Any  # _RawDetailBase 가 __init__ 에서 주입

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

    async def list_dart_ownership_events_by_stock(
        self,
        *,
        stock_id: int,
        since_date: Any | None = None,
    ) -> list[Any]:
        """DART 임원·주요주주 지분변동 이벤트 행(종목 기준, 윈도우 내, 최신순).

        ``dart_ownership_events.stock_id`` 는 nullable(수집 시 미해결이면 NULL — 부분 인덱스
        ``WHERE stock_id IS NOT NULL`` 가 방증)이라, stock_id 직접 매칭만 하면 미해결 이벤트가
        누락돼 src_dart 가 운영에서 조용히 비활성된다. 그래서 항상 채워지는 ``corp_code``(NOT NULL)
        를 ``dart_corp_codes`` 매핑으로 함께 매칭해 누락을 복구한다. ``report_date``(=rcept_dt,
        known_at) 의 PIT 게이트(``<= asof``)는 호출측 ``assemble_features`` 가 강제한다(#546 Phase 1).
        """
        return await self._connection.fetch(
            """
            SELECT
                e.stock_id,
                e.corp_code,
                e.rcept_no,
                e.report_date,
                e.holder_name,
                e.holder_type,
                e.shares,
                e.ratio,
                e.shares_delta,
                e.ratio_delta,
                e.report_reason
            FROM dart_ownership_events e
            WHERE (
                e.stock_id = $1
                OR e.corp_code IN (
                    SELECT corp_code FROM dart_corp_codes WHERE stock_id = $1
                )
            )
              AND ($2::date IS NULL OR e.report_date >= $2)
            ORDER BY e.report_date DESC, e.id DESC
            """,
            stock_id,
            since_date,
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
