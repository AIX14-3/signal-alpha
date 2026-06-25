from __future__ import annotations

import json
from typing import Any


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
            ON CONFLICT (source_type, external_id)
            DO UPDATE SET
                collector_run_id = EXCLUDED.collector_run_id,
                source_hash = EXCLUDED.source_hash,
                source_name = EXCLUDED.source_name,
                title = EXCLUDED.title,
                source_url = EXCLUDED.source_url,
                published_at = EXCLUDED.published_at,
                collect_status = EXCLUDED.collect_status,
                collect_error = EXCLUDED.collect_error,
                collected_at = NOW(),
                collector_ver = EXCLUDED.collector_ver
            RETURNING *, (xmax = 0) AS inserted
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
            _jsonb(extra_payload),
        )

    async def upsert_report_valuation_fact(
        self,
        *,
        raw_document_id: int,
        stock_id: int,
        ticker: str,
        broker: str | None = None,
        analyst: str | None = None,
        publish_date: Any | None = None,
        target_price: int | None = None,
        forward_eps_est: int | None = None,
        eps_fy: int | None = None,
        methodology: str = "unknown",
        applied_multiple: Any | None = None,
        implied_multiple: Any | None = None,
        peer_group: Any | None = None,
        category_tag: str | None = None,
        rerating_thesis: str | None = None,
        extraction_source: str = "rules",
        needs_review: bool = False,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO report_valuation_facts (
                raw_document_id,
                stock_id,
                ticker,
                broker,
                analyst,
                publish_date,
                target_price,
                forward_eps_est,
                eps_fy,
                methodology,
                applied_multiple,
                implied_multiple,
                peer_group,
                category_tag,
                rerating_thesis,
                extraction_source,
                needs_review
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
            ON CONFLICT (raw_document_id)
            DO UPDATE SET
                ticker = EXCLUDED.ticker,
                broker = EXCLUDED.broker,
                analyst = EXCLUDED.analyst,
                publish_date = EXCLUDED.publish_date,
                target_price = EXCLUDED.target_price,
                forward_eps_est = EXCLUDED.forward_eps_est,
                eps_fy = EXCLUDED.eps_fy,
                methodology = EXCLUDED.methodology,
                applied_multiple = EXCLUDED.applied_multiple,
                implied_multiple = EXCLUDED.implied_multiple,
                peer_group = EXCLUDED.peer_group,
                category_tag = EXCLUDED.category_tag,
                rerating_thesis = EXCLUDED.rerating_thesis,
                extraction_source = EXCLUDED.extraction_source,
                needs_review = EXCLUDED.needs_review,
                updated_at = NOW()
            RETURNING *
            """,
            raw_document_id,
            stock_id,
            ticker,
            broker,
            analyst,
            publish_date,
            target_price,
            forward_eps_est,
            eps_fy,
            methodology,
            applied_multiple,
            implied_multiple,
            _jsonb(peer_group or []),
            category_tag,
            rerating_thesis,
            extraction_source,
            needs_review,
        )

    async def list_report_valuation_facts(self, *, stock_id: int, limit: int = 20) -> list[Any]:
        return list(await self._connection.fetch(
            """
            SELECT
                raw_document_id,
                stock_id,
                ticker,
                broker,
                analyst,
                publish_date,
                target_price,
                forward_eps_est,
                eps_fy,
                methodology,
                applied_multiple,
                implied_multiple,
                peer_group,
                category_tag,
                rerating_thesis,
                extraction_source,
                needs_review,
                created_at,
                updated_at
            FROM report_valuation_facts
            WHERE stock_id = $1
            ORDER BY publish_date DESC NULLS LAST, raw_document_id DESC
            LIMIT $2
            """,
            stock_id,
            limit,
        ))

    async def delete_dart_test_data(
        self,
        *,
        stock_code: str,
        bgn_de: Any,
        end_de: Any,
    ) -> dict[str, int]:
        transaction = getattr(self._connection, "transaction", None)
        if transaction is None:
            return await self._delete_dart_test_data_counts(stock_code, bgn_de, end_de)
        async with self._connection.transaction():
            return await self._delete_dart_test_data_counts(stock_code, bgn_de, end_de)

    async def _delete_dart_test_data_counts(
        self,
        stock_code: str,
        bgn_de: Any,
        end_de: Any,
    ) -> dict[str, int]:
        deleted_score_history_count = await self._delete_dart_score_history(stock_code, bgn_de, end_de)
        deleted_final_signal_count = await self._delete_dart_final_signals(stock_code, bgn_de, end_de)
        deleted_agent_result_count = await self._delete_dart_agent_results(stock_code, bgn_de, end_de)
        deleted_analysis_result_count = await self._delete_dart_analysis_results(stock_code, bgn_de, end_de)
        deleted_signal_event_count = await self._delete_dart_signal_events(stock_code, bgn_de, end_de)
        deleted_raw_document_count = await self._delete_dart_raw_documents(stock_code, bgn_de, end_de)
        return {
            "deleted_score_history_count": deleted_score_history_count,
            "deleted_final_signal_count": deleted_final_signal_count,
            "deleted_agent_result_count": deleted_agent_result_count,
            "deleted_analysis_result_count": deleted_analysis_result_count,
            "deleted_signal_event_count": deleted_signal_event_count,
            "deleted_raw_document_count": deleted_raw_document_count,
        }

    async def _delete_dart_score_history(self, stock_code: str, bgn_de: Any, end_de: Any) -> int:
        return await self._delete_dart_count(
            """
            WITH target_results AS (
                SELECT id
                FROM analysis_results
                WHERE stock_id = (SELECT id FROM stocks WHERE ticker = $1)
                  AND run_key LIKE 'DART%'
                  AND analysis_date BETWEEN $2::DATE AND $3::DATE
            ),
            deleted AS (
                DELETE FROM score_history
                WHERE analysis_result_id IN (SELECT id FROM target_results)
                   OR final_signal_id IN (
                       SELECT id
                       FROM final_signals
                       WHERE analysis_result_id IN (SELECT id FROM target_results)
                   )
                RETURNING id
            )
            SELECT count(*) FROM deleted
            """,
            stock_code,
            bgn_de,
            end_de,
        )

    async def _delete_dart_final_signals(self, stock_code: str, bgn_de: Any, end_de: Any) -> int:
        return await self._delete_dart_count(
            """
            WITH target_results AS (
                SELECT id
                FROM analysis_results
                WHERE stock_id = (SELECT id FROM stocks WHERE ticker = $1)
                  AND run_key LIKE 'DART%'
                  AND analysis_date BETWEEN $2::DATE AND $3::DATE
            ),
            deleted AS (
                DELETE FROM final_signals
                WHERE analysis_result_id IN (SELECT id FROM target_results)
                RETURNING id
            )
            SELECT count(*) FROM deleted
            """,
            stock_code,
            bgn_de,
            end_de,
        )

    async def _delete_dart_agent_results(self, stock_code: str, bgn_de: Any, end_de: Any) -> int:
        return await self._delete_dart_count(
            """
            WITH target_results AS (
                SELECT id
                FROM analysis_results
                WHERE stock_id = (SELECT id FROM stocks WHERE ticker = $1)
                  AND run_key LIKE 'DART%'
                  AND analysis_date BETWEEN $2::DATE AND $3::DATE
            ),
            deleted AS (
                DELETE FROM agent_results
                WHERE result_id IN (SELECT id FROM target_results)
                RETURNING id
            )
            SELECT count(*) FROM deleted
            """,
            stock_code,
            bgn_de,
            end_de,
        )

    async def _delete_dart_analysis_results(self, stock_code: str, bgn_de: Any, end_de: Any) -> int:
        return await self._delete_dart_count(
            """
            WITH deleted AS (
                DELETE FROM analysis_results
                WHERE stock_id = (SELECT id FROM stocks WHERE ticker = $1)
                  AND run_key LIKE 'DART%'
                  AND analysis_date BETWEEN $2::DATE AND $3::DATE
                RETURNING id
            )
            SELECT count(*) FROM deleted
            """,
            stock_code,
            bgn_de,
            end_de,
        )

    async def _delete_dart_signal_events(self, stock_code: str, bgn_de: Any, end_de: Any) -> int:
        return await self._delete_dart_count(
            """
            WITH target_raw_documents AS (
                SELECT raw_documents.id
                FROM raw_documents
                WHERE raw_documents.stock_id = (SELECT id FROM stocks WHERE ticker = $1)
                  AND raw_documents.source_type = 'DART'
                  AND raw_documents.published_at::DATE BETWEEN $2::DATE AND $3::DATE
            ),
            deleted AS (
                DELETE FROM signal_events
                WHERE source_document_id IN (
                    SELECT id
                    FROM source_documents
                    WHERE raw_document_id IN (SELECT id FROM target_raw_documents)
                )
                RETURNING id
            )
            SELECT count(*) FROM deleted
            """,
            stock_code,
            bgn_de,
            end_de,
        )

    async def _delete_dart_raw_documents(self, stock_code: str, bgn_de: Any, end_de: Any) -> int:
        return await self._delete_dart_count(
            """
            WITH deleted AS (
                DELETE FROM raw_documents
                WHERE stock_id = (SELECT id FROM stocks WHERE ticker = $1)
                  AND source_type = 'DART'
                  AND published_at::DATE BETWEEN $2::DATE AND $3::DATE
                RETURNING id
            )
            SELECT count(*) FROM deleted
            """,
            stock_code,
            bgn_de,
            end_de,
        )

    async def _delete_dart_count(self, sql: str, stock_code: str, bgn_de: Any, end_de: Any) -> int:
        value = await self._connection.fetchval(sql, stock_code, bgn_de, end_de)
        return int(value or 0)


def _jsonb(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
