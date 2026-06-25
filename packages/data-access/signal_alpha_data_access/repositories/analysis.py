from __future__ import annotations

import json
from typing import Any


DEFAULT_DISCLAIMER = (
    "본 서비스가 제공하는 신호는 AI 에이전트의 데이터 분석 결과이며 "
    "투자 권유가 아닙니다. 투자 판단과 책임은 사용자 본인에게 있습니다."
)


class AnalysisRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def create_analysis_request(
        self,
        *,
        stock_id: int,
        user_id: int | None = None,
        analysis_mode: str = "full",
        ip_address: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO analysis_requests (
                user_id,
                stock_id,
                analysis_mode,
                ip_address
            )
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            user_id,
            stock_id,
            analysis_mode,
            ip_address,
        )

    async def complete_analysis_request(
        self,
        *,
        request_id: int,
        status: str,
        error_message: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            UPDATE analysis_requests
            SET
                status = $2,
                completed_at = NOW(),
                error_message = $3
            WHERE id = $1
            RETURNING *
            """,
            request_id,
            status,
            error_message,
        )

    async def upsert_analysis_result(
        self,
        *,
        stock_id: int,
        analysis_date: Any,
        run_key: str,
        source_signal_event_ids: list[int],
        base_score: Any,
        analysis_mode: str = "full",
        version: str = "1.0",
        request_id: int | None = None,
        pre_xgb_score: Any | None = None,
        xgb_adj: Any | None = None,
        warning: str | None = None,
        disclaimer: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO analysis_results (
                request_id,
                stock_id,
                analysis_date,
                run_key,
                source_signal_event_ids,
                base_score,
                pre_xgb_score,
                xgb_adj,
                analysis_mode,
                warning,
                disclaimer,
                version
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (stock_id, analysis_date, analysis_mode, run_key, version)
            DO UPDATE SET
                request_id = EXCLUDED.request_id,
                source_signal_event_ids = EXCLUDED.source_signal_event_ids,
                base_score = EXCLUDED.base_score,
                pre_xgb_score = EXCLUDED.pre_xgb_score,
                xgb_adj = EXCLUDED.xgb_adj,
                warning = EXCLUDED.warning,
                disclaimer = EXCLUDED.disclaimer
            RETURNING *
            """,
            request_id,
            stock_id,
            analysis_date,
            run_key,
            source_signal_event_ids,
            base_score,
            pre_xgb_score,
            xgb_adj,
            analysis_mode,
            warning,
            disclaimer or DEFAULT_DISCLAIMER,
            version,
        )

    async def upsert_agent_result(
        self,
        *,
        result_id: int,
        stock_id: int,
        debate_method: str,
        method_score: Any,
        method_signal: str,
        method_detail: Any,
        source_signal_event_ids: list[int] | None = None,
        reliability_score: Any | None = None,
        evidence_quality: Any | None = None,
        llm_model: str | None = None,
        prompt_ver: str = "1.0",
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO agent_results (
                result_id,
                stock_id,
                debate_method,
                source_signal_event_ids,
                method_score,
                method_signal,
                method_detail,
                reliability_score,
                evidence_quality,
                llm_model,
                prompt_ver
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (result_id, debate_method)
            DO UPDATE SET
                source_signal_event_ids = EXCLUDED.source_signal_event_ids,
                method_score = EXCLUDED.method_score,
                method_signal = EXCLUDED.method_signal,
                method_detail = EXCLUDED.method_detail,
                reliability_score = EXCLUDED.reliability_score,
                evidence_quality = EXCLUDED.evidence_quality,
                llm_model = EXCLUDED.llm_model,
                prompt_ver = EXCLUDED.prompt_ver
            RETURNING *
            """,
            result_id,
            stock_id,
            debate_method,
            source_signal_event_ids,
            method_score,
            method_signal,
            _jsonb(method_detail),
            reliability_score,
            evidence_quality,
            llm_model,
            prompt_ver,
        )

    async def list_dart_analysis_results(
        self,
        *,
        stock_code: str,
        analysis_date: Any | None = None,
        limit: int = 20,
    ) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                ar.id,
                ar.stock_id,
                stocks.ticker AS stock_code,
                stocks.name AS stock_name,
                ar.analysis_date,
                ar.run_key,
                ar.analysis_mode,
                ar.version,
                ar.source_signal_event_ids,
                ar.base_score,
                ar.pre_xgb_score,
                ar.xgb_adj,
                ar.warning,
                ar.disclaimer,
                ar.created_at,
                COALESCE(agent_results.items, '[]'::JSONB) AS agent_results,
                COALESCE(signal_events.items, '[]'::JSONB) AS signal_events
            FROM analysis_results ar
            INNER JOIN stocks
                ON stocks.id = ar.stock_id
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
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
                    ORDER BY agent_results.debate_method
                ) AS items
                FROM agent_results
                WHERE agent_results.result_id = ar.id
            ) agent_results ON TRUE
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
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
                        'needs_review', signal_events.needs_review
                    )
                    ORDER BY signal_events.event_date DESC, signal_events.id DESC
                ) AS items
                FROM signal_events
                WHERE signal_events.id = ANY(ar.source_signal_event_ids)
                  AND signal_events.source_type = 'DART'
            ) signal_events ON TRUE
            WHERE stocks.ticker = $1
              AND ($2::DATE IS NULL OR ar.analysis_date = $2::DATE)
              AND ar.run_key LIKE 'DART%'
            ORDER BY ar.analysis_date DESC, ar.created_at DESC, ar.id DESC
            LIMIT $3
            """,
            stock_code,
            analysis_date,
            limit,
        )

    async def list_agent_results_for_aggregation(self, analysis_result_ids: list[int]) -> list[Any]:
        if not analysis_result_ids:
            return []
        return await self._connection.fetch(
            """
            SELECT
                analysis_results.id AS analysis_result_id,
                analysis_results.stock_id,
                analysis_results.analysis_date,
                analysis_results.run_key AS analysis_run_key,
                analysis_results.analysis_mode,
                analysis_results.version AS analysis_version,
                analysis_results.source_signal_event_ids AS analysis_source_signal_event_ids,
                agent_results.id AS agent_result_id,
                agent_results.debate_method,
                agent_results.source_signal_event_ids AS agent_source_signal_event_ids,
                agent_results.method_score,
                agent_results.method_signal,
                agent_results.method_detail,
                agent_results.reliability_score,
                agent_results.evidence_quality,
                agent_results.llm_model,
                agent_results.prompt_ver
            FROM agent_results
            INNER JOIN analysis_results
                ON analysis_results.id = agent_results.result_id
            WHERE agent_results.result_id = ANY($1::BIGINT[])
            ORDER BY analysis_results.analysis_date DESC, agent_results.id DESC
            """,
            analysis_result_ids,
        )

    async def list_final_signals_by_stock(
        self,
        *,
        stock_code: str,
        signal_date: Any | None = None,
        bgn_de: Any | None = None,
        end_de: Any | None = None,
        run_key: str = "AGGREGATED",
        limit: int = 20,
    ) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                final_signals.id,
                final_signals.stock_id,
                stocks.ticker AS stock_code,
                stocks.name AS stock_name,
                final_signals.analysis_result_id,
                final_signals.signal_date,
                final_signals.run_key,
                final_signals.version,
                final_signals.final_score,
                final_signals.confidence,
                final_signals.signal,
                final_signals.source_agreement,
                final_signals.warning_level,
                final_signals.score_breakdown,
                final_signals.summary,
                final_signals.bull_point,
                final_signals.bear_point,
                final_signals.disclaimer,
                final_signals.needs_review,
                final_signals.min_plan_required,
                final_signals.is_published,
                final_signals.published_at,
                final_signals.consensus_score,
                final_signals.positive_evidence,
                final_signals.caution_evidence,
                final_signals.created_at
            FROM final_signals
            INNER JOIN stocks
                ON stocks.id = final_signals.stock_id
            WHERE stocks.ticker = $1
              AND ($2::DATE IS NULL OR final_signals.signal_date = $2::DATE)
              AND ($3::DATE IS NULL OR final_signals.signal_date >= $3::DATE)
              AND ($4::DATE IS NULL OR final_signals.signal_date <= $4::DATE)
              AND final_signals.run_key = $5
            ORDER BY final_signals.signal_date DESC,
                     final_signals.created_at DESC,
                     final_signals.id DESC
            LIMIT $6
            """,
            stock_code,
            signal_date,
            bgn_de,
            end_de,
            run_key,
            limit,
        )

    async def upsert_final_signal(
        self,
        *,
        stock_id: int,
        analysis_result_id: int,
        signal_date: Any,
        run_key: str,
        version: str,
        final_score: Any,
        confidence: Any,
        signal: str,
        source_agreement: str,
        score_breakdown: Any,
        summary: str,
        warning_level: str = "NORMAL",
        bull_point: str | None = None,
        bear_point: str | None = None,
        disclaimer: str | None = None,
        needs_review: bool = False,
        min_plan_required: str = "free",
        is_published: bool = False,
        published_at: Any | None = None,
        consensus_score: Any = None,
        positive_evidence: Any = None,
        caution_evidence: Any = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO final_signals (
                stock_id,
                analysis_result_id,
                signal_date,
                run_key,
                version,
                final_score,
                confidence,
                signal,
                source_agreement,
                warning_level,
                score_breakdown,
                summary,
                bull_point,
                bear_point,
                disclaimer,
                needs_review,
                min_plan_required,
                is_published,
                published_at,
                consensus_score,
                positive_evidence,
                caution_evidence
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16, $17, $18, $19,
                $20, $21, $22
            )
            ON CONFLICT (stock_id, signal_date, run_key, version)
            DO UPDATE SET
                analysis_result_id = EXCLUDED.analysis_result_id,
                final_score = EXCLUDED.final_score,
                confidence = EXCLUDED.confidence,
                signal = EXCLUDED.signal,
                source_agreement = EXCLUDED.source_agreement,
                warning_level = EXCLUDED.warning_level,
                score_breakdown = EXCLUDED.score_breakdown,
                summary = EXCLUDED.summary,
                bull_point = EXCLUDED.bull_point,
                bear_point = EXCLUDED.bear_point,
                disclaimer = EXCLUDED.disclaimer,
                needs_review = EXCLUDED.needs_review,
                min_plan_required = EXCLUDED.min_plan_required,
                is_published = EXCLUDED.is_published,
                published_at = EXCLUDED.published_at,
                consensus_score = EXCLUDED.consensus_score,
                positive_evidence = EXCLUDED.positive_evidence,
                caution_evidence = EXCLUDED.caution_evidence
            RETURNING *
            """,
            stock_id,
            analysis_result_id,
            signal_date,
            run_key,
            version,
            final_score,
            confidence,
            signal,
            source_agreement,
            warning_level,
            _jsonb(score_breakdown),
            summary,
            bull_point,
            bear_point,
            disclaimer or DEFAULT_DISCLAIMER,
            needs_review,
            min_plan_required,
            is_published,
            published_at,
            consensus_score,
            _jsonb(positive_evidence),
            _jsonb(caution_evidence),
        )


    async def apply_risk_veto(self, *, final_signal_id: int) -> Any:
        """리스크 veto — 치명 키워드 탐지 시 해당 final_signal 발행 보류.

        점수/방향은 그대로 두고 발행 플래그만 차단(is_published=FALSE), 검토 필요 표시,
        경보 WARNING 승격. veto 사유는 validation_logs에 별도 기록한다.
        """
        return await self._connection.fetchrow(
            """
            UPDATE final_signals
            SET
                is_published = FALSE,
                published_at = NULL,
                needs_review = TRUE,
                warning_level = 'WARNING'
            WHERE id = $1
            RETURNING *
            """,
            final_signal_id,
        )

    async def get_final_signal_by_id(self, *, final_signal_id: int) -> Any:
        """발행 여부와 무관하게 final_signal 1건 조회(끝단 종합은 vetoed/미발행도 설명)."""
        return await self._connection.fetchrow(
            "SELECT * FROM final_signals WHERE id = $1",
            final_signal_id,
        )

    async def update_final_signal_narrative(
        self,
        *,
        final_signal_id: int,
        summary: str,
        bull_point: str | None = None,
        bear_point: str | None = None,
    ) -> Any:
        """끝단 LLM 종합 결과(설명)만 갱신 — 점수/방향/발행 플래그는 건드리지 않는다."""
        return await self._connection.fetchrow(
            """
            UPDATE final_signals
            SET
                summary = $2,
                bull_point = $3,
                bear_point = $4
            WHERE id = $1
            RETURNING *
            """,
            final_signal_id,
            summary,
            bull_point,
            bear_point,
        )


def _jsonb(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
