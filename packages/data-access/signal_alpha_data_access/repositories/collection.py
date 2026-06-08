from __future__ import annotations

from typing import Any, Iterable


class CollectionRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def create_collector_run(self, collector_type: str, run_mode: str) -> int:
        return await self._connection.fetchval(
            """
            INSERT INTO collector_runs (collector_type, run_mode)
            VALUES ($1, $2)
            RETURNING id
            """,
            collector_type,
            run_mode,
        )

    async def finish_collector_run(
        self,
        *,
        run_id: int,
        status: str,
        collected_count: int = 0,
        inserted_count: int = 0,
        skipped_count: int = 0,
        failed_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        await self._connection.execute(
            """
            UPDATE collector_runs
            SET
                status = $2,
                finished_at = NOW(),
                collected_count = $3,
                inserted_count = $4,
                skipped_count = $5,
                failed_count = $6,
                error_message = $7
            WHERE id = $1
            """,
            run_id,
            status,
            collected_count,
            inserted_count,
            skipped_count,
            failed_count,
            error_message,
        )

    async def upsert_raw_document(
        self,
        *,
        stock_id: int,
        collector_run_id: int | None,
        source_type: str,
        source_name: str,
        external_id: str,
        source_hash: str,
        title: str,
        source_url: str | None,
        published_at: Any,
        collect_status: str = "success",
        collect_error: str | None = None,
        collector_ver: str = "1.0",
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO raw_documents (
                stock_id,
                collector_run_id,
                source_type,
                source_name,
                external_id,
                source_hash,
                title,
                source_url,
                published_at,
                collect_status,
                collect_error,
                collector_ver
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (source_hash)
            DO UPDATE SET
                collector_run_id = EXCLUDED.collector_run_id,
                source_name = EXCLUDED.source_name,
                title = EXCLUDED.title,
                source_url = EXCLUDED.source_url,
                published_at = EXCLUDED.published_at,
                collect_status = EXCLUDED.collect_status,
                collect_error = EXCLUDED.collect_error,
                collected_at = NOW(),
                collector_ver = EXCLUDED.collector_ver
            RETURNING *
            """,
            stock_id,
            collector_run_id,
            source_type,
            source_name,
            external_id,
            source_hash,
            title,
            source_url,
            published_at,
            collect_status,
            collect_error,
            collector_ver,
        )

    async def upsert_report_detail(
        self,
        *,
        raw_document_id: int,
        stock_id: int,
        securities_firm: str,
        publish_date: Any,
        analyst_name: str | None = None,
        investment_opinion: str | None = None,
        target_price: int | None = None,
        previous_target_price: int | None = None,
        current_price_at_publish: int | None = None,
        upside_pct: Any | None = None,
        has_pdf: bool = False,
        pdf_url: str | None = None,
        local_file_path: str | None = None,
        extracted_text: str | None = None,
        extracted_text_path: str | None = None,
        parsing_status: str = "pending",
        parsing_error: str | None = None,
        extra_payload: Any | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO report_raw_details (
                raw_document_id,
                stock_id,
                securities_firm,
                analyst_name,
                publish_date,
                investment_opinion,
                target_price,
                previous_target_price,
                current_price_at_publish,
                upside_pct,
                has_pdf,
                pdf_url,
                local_file_path,
                extracted_text,
                extracted_text_path,
                parsing_status,
                parsing_error,
                extra_payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
            ON CONFLICT (raw_document_id)
            DO UPDATE SET
                securities_firm = EXCLUDED.securities_firm,
                analyst_name = EXCLUDED.analyst_name,
                publish_date = EXCLUDED.publish_date,
                investment_opinion = EXCLUDED.investment_opinion,
                target_price = EXCLUDED.target_price,
                previous_target_price = EXCLUDED.previous_target_price,
                current_price_at_publish = EXCLUDED.current_price_at_publish,
                upside_pct = EXCLUDED.upside_pct,
                has_pdf = EXCLUDED.has_pdf,
                pdf_url = EXCLUDED.pdf_url,
                local_file_path = EXCLUDED.local_file_path,
                extracted_text = EXCLUDED.extracted_text,
                extracted_text_path = EXCLUDED.extracted_text_path,
                parsing_status = EXCLUDED.parsing_status,
                parsing_error = EXCLUDED.parsing_error,
                extra_payload = EXCLUDED.extra_payload
            RETURNING *
            """,
            raw_document_id,
            stock_id,
            securities_firm,
            analyst_name,
            publish_date,
            investment_opinion,
            target_price,
            previous_target_price,
            current_price_at_publish,
            upside_pct,
            has_pdf,
            pdf_url,
            local_file_path,
            extracted_text,
            extracted_text_path,
            parsing_status,
            parsing_error,
            extra_payload,
        )

    async def replace_report_chunks(
        self,
        *,
        raw_document_id: int,
        stock_id: int,
        chunks: Iterable[str],
        token_counts: Iterable[int | None] | None = None,
    ) -> None:
        await self._connection.execute(
            """
            DELETE FROM report_chunks
            WHERE raw_document_id = $1
            """,
            raw_document_id,
        )

        chunk_list = list(chunks)
        token_count_list = list(token_counts) if token_counts is not None else [None] * len(chunk_list)
        rows = [
            (raw_document_id, stock_id, index, chunk_text, token_count_list[index])
            for index, chunk_text in enumerate(chunk_list)
        ]
        if not rows:
            return

        await self._connection.executemany(
            """
            INSERT INTO report_chunks (
                raw_document_id,
                stock_id,
                chunk_index,
                chunk_text,
                token_count
            )
            VALUES ($1, $2, $3, $4, $5)
            """,
            rows,
        )
