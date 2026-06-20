from __future__ import annotations

from typing import Any


class SignalRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def get_current_by_ticker(self, ticker: str) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT
                final_signals.*,
                stocks.ticker,
                stocks.name,
                stocks.market,
                analysis_results.analysis_mode,
                analysis_results.base_score,
                analysis_results.warning AS analysis_warning
            FROM final_signals
            INNER JOIN stocks
                ON stocks.id = final_signals.stock_id
            INNER JOIN analysis_results
                ON analysis_results.id = final_signals.analysis_result_id
            WHERE stocks.ticker = $1
              AND final_signals.is_current = TRUE
              AND final_signals.is_published = TRUE
            ORDER BY final_signals.published_at DESC NULLS LAST,
                     final_signals.created_at DESC
            LIMIT 1
            """,
            ticker.strip(),
        )

    async def get_detail_by_id(self, signal_id: int) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT
                final_signals.*,
                stocks.ticker,
                stocks.name,
                stocks.market,
                stocks.sector,
                analysis_results.analysis_date,
                analysis_results.analysis_mode,
                analysis_results.run_key AS analysis_run_key,
                analysis_results.version AS analysis_version,
                analysis_results.base_score,
                analysis_results.warning AS analysis_warning,
                analysis_results.source_signal_event_ids,
                COALESCE(agent_results.items, '[]'::JSONB) AS agent_results,
                COALESCE(signal_events.items, '[]'::JSONB) AS signal_events
            FROM final_signals
            INNER JOIN stocks
                ON stocks.id = final_signals.stock_id
            INNER JOIN analysis_results
                ON analysis_results.id = final_signals.analysis_result_id
            LEFT JOIN LATERAL (
                SELECT JSONB_AGG(
                    JSONB_BUILD_OBJECT(
                        'id', agent_results.id,
                        'debate_method', agent_results.debate_method,
                        'method_score', agent_results.method_score,
                        'method_signal', agent_results.method_signal,
                        'method_detail', agent_results.method_detail,
                        'source_signal_event_ids', agent_results.source_signal_event_ids,
                        'reliability_score', agent_results.reliability_score,
                        'evidence_quality', agent_results.evidence_quality,
                        'llm_model', agent_results.llm_model,
                        'prompt_ver', agent_results.prompt_ver,
                        'created_at', agent_results.created_at
                    )
                    ORDER BY agent_results.debate_method, agent_results.id
                ) AS items
                FROM agent_results
                WHERE agent_results.result_id = analysis_results.id
            ) agent_results ON TRUE
            LEFT JOIN LATERAL (
                SELECT JSONB_AGG(
                    JSONB_BUILD_OBJECT(
                        'id', signal_events.id,
                        'source_document_id', signal_events.source_document_id,
                        'source_type', signal_events.source_type,
                        'event_type', signal_events.event_type,
                        'event_date', signal_events.event_date,
                        'signal_direction', signal_events.signal_direction,
                        'impact_level', signal_events.impact_level,
                        'title', signal_events.title,
                        'summary', signal_events.summary,
                        'evidence_url', signal_events.evidence_url,
                        'needs_review', signal_events.needs_review,
                        'source_name', source_documents.source_name,
                        'source_url', source_documents.source_url,
                        'is_official', source_documents.is_official
                    )
                    ORDER BY
                        CASE signal_events.impact_level
                            WHEN 'high' THEN 0
                            WHEN 'medium' THEN 1
                            ELSE 2
                        END,
                        signal_events.event_date DESC,
                        signal_events.id ASC
                ) AS items
                FROM signal_events
                LEFT JOIN source_documents
                    ON source_documents.id = signal_events.source_document_id
                WHERE signal_events.id = ANY(analysis_results.source_signal_event_ids)
            ) signal_events ON TRUE
            WHERE final_signals.id = $1
              AND final_signals.is_current = TRUE
              AND final_signals.is_published = TRUE
            LIMIT 1
            """,
            signal_id,
        )

    async def list_current_published(self, limit: int = 50) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                final_signals.*,
                stocks.ticker,
                stocks.name,
                stocks.market
            FROM final_signals
            INNER JOIN stocks
                ON stocks.id = final_signals.stock_id
            WHERE final_signals.is_current = TRUE
              AND final_signals.is_published = TRUE
            ORDER BY final_signals.published_at DESC NULLS LAST,
                     final_signals.created_at DESC
            LIMIT $1
            """,
            limit,
        )

    async def list_current_by_stock_ids(self, stock_ids: list[int]) -> list[Any]:
        if not stock_ids:
            return []
        return await self._connection.fetch(
            """
            SELECT
                final_signals.*,
                stocks.ticker,
                stocks.name,
                stocks.market
            FROM final_signals
            INNER JOIN stocks
                ON stocks.id = final_signals.stock_id
            WHERE final_signals.stock_id = ANY($1::BIGINT[])
              AND final_signals.is_current = TRUE
              AND final_signals.is_published = TRUE
            ORDER BY final_signals.stock_id ASC,
                     final_signals.published_at DESC NULLS LAST,
                     final_signals.created_at DESC
            """,
            stock_ids,
        )
