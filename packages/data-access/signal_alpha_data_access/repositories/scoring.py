from __future__ import annotations

from typing import Any


class ScoringRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_quant_score(
        self,
        *,
        result_id: int,
        stock_id: int,
        score_breakdown: Any,
        overall_score: Any,
        available_sources: list[str],
        source_agreement: str,
        missing_sources: list[str] | None = None,
        failed_agent_count: int = 0,
        alert_level: int = 0,
        score_cap_applied: bool = False,
        score_cap_reason: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO quant_scores (
                result_id, stock_id, score_breakdown, overall_score,
                available_sources, missing_sources, source_agreement,
                failed_agent_count, alert_level, score_cap_applied, score_cap_reason
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (result_id)
            DO UPDATE SET
                score_breakdown = EXCLUDED.score_breakdown,
                overall_score = EXCLUDED.overall_score,
                available_sources = EXCLUDED.available_sources,
                missing_sources = EXCLUDED.missing_sources,
                source_agreement = EXCLUDED.source_agreement,
                failed_agent_count = EXCLUDED.failed_agent_count,
                alert_level = EXCLUDED.alert_level,
                score_cap_applied = EXCLUDED.score_cap_applied,
                score_cap_reason = EXCLUDED.score_cap_reason
            RETURNING *
            """,
            result_id,
            stock_id,
            score_breakdown,
            overall_score,
            available_sources,
            missing_sources,
            source_agreement,
            failed_agent_count,
            alert_level,
            score_cap_applied,
            score_cap_reason,
        )

    async def upsert_ta_score(
        self,
        *,
        result_id: int,
        stock_id: int,
        ta_score: Any | None = None,
        ta_detail: Any | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO ta_scores (result_id, stock_id, ta_score, ta_detail)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (result_id)
            DO UPDATE SET
                ta_score = EXCLUDED.ta_score,
                ta_detail = EXCLUDED.ta_detail
            RETURNING *
            """,
            result_id,
            stock_id,
            ta_score,
            ta_detail,
        )

    async def upsert_ai_score(
        self,
        *,
        result_id: int,
        stock_id: int,
        dart_agent_score: Any | None = None,
        report_agent_score: Any | None = None,
        alt_agent_score: Any | None = None,
        dart_confidence: Any | None = None,
        report_confidence: Any | None = None,
        alt_confidence: Any | None = None,
        validation_log: Any | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO ai_scores (
                result_id, stock_id, dart_agent_score, report_agent_score,
                alt_agent_score, dart_confidence, report_confidence,
                alt_confidence, validation_log
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (result_id)
            DO UPDATE SET
                dart_agent_score = EXCLUDED.dart_agent_score,
                report_agent_score = EXCLUDED.report_agent_score,
                alt_agent_score = EXCLUDED.alt_agent_score,
                dart_confidence = EXCLUDED.dart_confidence,
                report_confidence = EXCLUDED.report_confidence,
                alt_confidence = EXCLUDED.alt_confidence,
                validation_log = EXCLUDED.validation_log
            RETURNING *
            """,
            result_id,
            stock_id,
            dart_agent_score,
            report_agent_score,
            alt_agent_score,
            dart_confidence,
            report_confidence,
            alt_confidence,
            validation_log,
        )

    async def upsert_xgb_model_version(
        self,
        *,
        model_version: str,
        trained_at: Any | None = None,
        feature_names: Any | None = None,
        feature_importance: Any | None = None,
        validation_score: Any | None = None,
        is_active: bool = False,
        training_samples: int | None = None,
        notes: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO xgb_model_versions (
                model_version, trained_at, feature_names, feature_importance,
                validation_score, is_active, training_samples, notes
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (model_version)
            DO UPDATE SET
                trained_at = EXCLUDED.trained_at,
                feature_names = EXCLUDED.feature_names,
                feature_importance = EXCLUDED.feature_importance,
                validation_score = EXCLUDED.validation_score,
                is_active = EXCLUDED.is_active,
                training_samples = EXCLUDED.training_samples,
                notes = EXCLUDED.notes
            RETURNING *
            """,
            model_version,
            trained_at,
            feature_names,
            feature_importance,
            validation_score,
            is_active,
            training_samples,
            notes,
        )

    async def upsert_ml_score(
        self,
        *,
        result_id: int,
        stock_id: int,
        ml_score: Any,
        calibrated_score: Any,
        model_version_id: int | None = None,
        prediction_label: str | None = None,
        feature_importance: Any | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO ml_scores (
                result_id, stock_id, model_version_id, ml_score,
                calibrated_score, prediction_label, feature_importance
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (result_id)
            DO UPDATE SET
                model_version_id = EXCLUDED.model_version_id,
                ml_score = EXCLUDED.ml_score,
                calibrated_score = EXCLUDED.calibrated_score,
                prediction_label = EXCLUDED.prediction_label,
                feature_importance = EXCLUDED.feature_importance
            RETURNING *
            """,
            result_id,
            stock_id,
            model_version_id,
            ml_score,
            calibrated_score,
            prediction_label,
            feature_importance,
        )

    async def record_score_history(
        self,
        *,
        stock_id: int,
        signal_date: Any,
        final_score: Any,
        final_signal_id: int | None = None,
        analysis_result_id: int | None = None,
        pre_xgb_score: Any | None = None,
        reliability_score: Any | None = None,
        model_version: str | None = None,
        scoring_version: str | None = None,
        reanalysis_reason: str | None = None,
    ) -> int:
        if final_signal_id is None and analysis_result_id is None:
            raise ValueError("final_signal_id or analysis_result_id is required.")

        return await self._connection.fetchval(
            """
            INSERT INTO score_history (
                stock_id, final_signal_id, analysis_result_id, signal_date,
                final_score, pre_xgb_score, reliability_score, model_version,
                scoring_version, reanalysis_reason
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id
            """,
            stock_id,
            final_signal_id,
            analysis_result_id,
            signal_date,
            final_score,
            pre_xgb_score,
            reliability_score,
            model_version,
            scoring_version,
            reanalysis_reason,
        )
