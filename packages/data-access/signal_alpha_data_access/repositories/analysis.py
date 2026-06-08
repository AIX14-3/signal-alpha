from __future__ import annotations

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
            method_detail,
            reliability_score,
            evidence_quality,
            llm_model,
            prompt_ver,
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
                published_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16, $17, $18, $19
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
                published_at = EXCLUDED.published_at
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
            score_breakdown,
            summary,
            bull_point,
            bear_point,
            disclaimer or DEFAULT_DISCLAIMER,
            needs_review,
            min_plan_required,
            is_published,
            published_at,
        )
