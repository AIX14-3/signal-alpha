from __future__ import annotations

from typing import Any


class BacktestRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def create_result(
        self,
        *,
        final_signal_id: int,
        stock_id: int,
        signal_date: Any,
        signal_score: Any,
        signal_value: str,
        price_at_signal: Any,
        confidence_band: str | None = None,
        source_agreement: str | None = None,
    ) -> int:
        return await self._connection.fetchval(
            """
            INSERT INTO backtest_results (
                final_signal_id, stock_id, signal_date, signal_score,
                signal_value, price_at_signal, confidence_band, source_agreement
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            final_signal_id,
            stock_id,
            signal_date,
            signal_score,
            signal_value,
            price_at_signal,
            confidence_band,
            source_agreement,
        )

    async def list_pending_checks(self, limit: int = 100) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT *
            FROM backtest_results
            WHERE checked_at IS NULL
            ORDER BY signal_date ASC, id ASC
            LIMIT $1
            """,
            limit,
        )

    async def list_by_stock(self, *, stock_id: int, limit: int = 100) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT *
            FROM backtest_results
            WHERE stock_id = $1
            ORDER BY signal_date DESC, id DESC
            LIMIT $2
            """,
            stock_id,
            limit,
        )

    async def complete_result(
        self,
        *,
        result_id: int,
        price_after_5d: Any,
        change_pct_5d: Any,
        is_hit: bool,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            UPDATE backtest_results
            SET
                price_after_5d = $2,
                change_pct_5d = $3,
                checked_at = NOW(),
                is_hit = $4
            WHERE id = $1
            RETURNING *
            """,
            result_id,
            price_after_5d,
            change_pct_5d,
            is_hit,
        )

    async def get_signal_posthoc_alignment_summary(self) -> list[Any]:
        """전체 발행 신호 backtest 결과의 5거래일 사후 정합성을 집계한다."""
        return await self._connection.fetch(
            """
            WITH classified AS (
                SELECT
                    '5td' AS horizon,
                    signal_value AS direction,
                    id AS signal_id,
                    signal_date AS outcome_trade_date,
                    checked_at,
                    is_hit
                FROM backtest_results
                WHERE signal_value IN ('positive', 'negative')
            ),
            horizon_summary AS (
                SELECT
                    '5td' AS horizon,
                    COUNT(signal_id)
                        FILTER (WHERE is_hit IS NOT NULL)::INT AS confirmed_count,
                    COUNT(signal_id)
                        FILTER (WHERE is_hit IS TRUE)::INT AS aligned_count,
                    COUNT(signal_id)
                        FILTER (WHERE is_hit IS FALSE)::INT AS not_aligned_count,
                    COUNT(signal_id)
                        FILTER (WHERE checked_at IS NULL OR is_hit IS NULL)::INT AS pending_count,
                    ROUND(
                        COUNT(signal_id)
                            FILTER (WHERE is_hit IS TRUE)::NUMERIC
                        / NULLIF(
                            COUNT(signal_id)
                                FILTER (WHERE is_hit IS NOT NULL),
                            0
                        )
                        * 100,
                        1
                    ) AS alignment_rate,
                    MIN(outcome_trade_date)
                        FILTER (WHERE is_hit IS NOT NULL) AS first_outcome_trade_date,
                    MAX(outcome_trade_date)
                        FILTER (WHERE is_hit IS NOT NULL) AS last_outcome_trade_date,
                    MAX(checked_at) AS checked_at
                FROM classified
            ),
            direction_summary AS (
                SELECT
                    classified.horizon,
                    classified.direction,
                    COUNT(classified.signal_id)
                        FILTER (WHERE classified.is_hit IS NOT NULL)::INT AS confirmed_count,
                    COUNT(classified.signal_id)
                        FILTER (WHERE classified.is_hit IS TRUE)::INT AS aligned_count,
                    COUNT(classified.signal_id)
                        FILTER (WHERE classified.is_hit IS FALSE)::INT AS not_aligned_count,
                    COUNT(classified.signal_id)
                        FILTER (WHERE classified.checked_at IS NULL OR classified.is_hit IS NULL)::INT AS pending_count,
                    ROUND(
                        COUNT(classified.signal_id)
                            FILTER (WHERE classified.is_hit IS TRUE)::NUMERIC
                        / NULLIF(
                            COUNT(classified.signal_id)
                                FILTER (WHERE classified.is_hit IS NOT NULL),
                            0
                        )
                        * 100,
                        1
                    ) AS alignment_rate
                FROM classified
                GROUP BY classified.horizon, classified.direction
            ),
            direction_breakdown AS (
                SELECT
                    direction_summary.horizon,
                    JSONB_AGG(
                        JSONB_BUILD_OBJECT(
                            'direction', direction_summary.direction,
                            'confirmed_count', direction_summary.confirmed_count,
                            'aligned_count', direction_summary.aligned_count,
                            'not_aligned_count', direction_summary.not_aligned_count,
                            'pending_count', direction_summary.pending_count,
                            'alignment_rate', direction_summary.alignment_rate
                        )
                        ORDER BY CASE direction_summary.direction
                            WHEN 'positive' THEN 1
                            WHEN 'negative' THEN 2
                            ELSE 99
                        END
                    ) AS direction_breakdown
                FROM direction_summary
                GROUP BY direction_summary.horizon
            )
            SELECT
                horizon_summary.*,
                COALESCE(direction_breakdown.direction_breakdown, '[]'::JSONB) AS direction_breakdown
            FROM horizon_summary
            LEFT JOIN direction_breakdown
              ON direction_breakdown.horizon = horizon_summary.horizon
            """
        )
