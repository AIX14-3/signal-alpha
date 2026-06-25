from __future__ import annotations

import json
from typing import Any


class RecommendationRepository:
    """recommendations 적재/조회 — ML/DL/LLM 라인 산출을 결정론 추천 점수로 랭킹한 원장.

    종목/asof/run_key 당 1행을 자연키로 멱등 upsert 한다. 추천은 발행 신호의 확신·안정성 기준
    랭킹이며 투자 조언이 아니다(매수/매도 지시 없음).
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_recommendation(
        self,
        *,
        stock_id: int,
        asof_date: Any,
        run_key: str,
        rank: int,
        recommendation_score: float,
        basis: str,
        signal: str | None,
        final_score: float | None,
        confidence: float | None,
        combined_vol: float | None,
        components: dict[str, Any] | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO recommendations (
                stock_id, asof_date, run_key, rank, recommendation_score,
                basis, signal, final_score, confidence, combined_vol, components
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (stock_id, asof_date, run_key)
            DO UPDATE SET
                rank = EXCLUDED.rank,
                recommendation_score = EXCLUDED.recommendation_score,
                basis = EXCLUDED.basis,
                signal = EXCLUDED.signal,
                final_score = EXCLUDED.final_score,
                confidence = EXCLUDED.confidence,
                combined_vol = EXCLUDED.combined_vol,
                components = EXCLUDED.components,
                -- created_at = "마지막 산출 시각"(감사용 최초생성 시각 아님). 재계산 원장이라
                -- 형제 repo(meta_signals·ml_inferences)와 동일하게 upsert 시 NOW() 로 갱신한다.
                created_at = NOW()
            RETURNING *
            """,
            stock_id,
            asof_date,
            run_key,
            rank,
            recommendation_score,
            basis,
            signal,
            final_score,
            confidence,
            combined_vol,
            json.dumps(components or {}),
        )

    async def list_for_run(
        self,
        *,
        asof_date: Any,
        run_key: str = "REC",
        limit: int | None = None,
    ) -> list[Any]:
        """한 asof/run_key 추천 랭킹(rank 오름차순). limit 미지정 시 전체."""
        return await self._connection.fetch(
            """
            SELECT recommendations.*, stocks.ticker, stocks.name
            FROM recommendations
            INNER JOIN stocks ON stocks.id = recommendations.stock_id
            WHERE asof_date = $1 AND run_key = $2
            ORDER BY rank ASC
            {limit_clause}
            """.format(limit_clause="LIMIT $3" if limit else ""),
            *([asof_date, run_key, limit] if limit else [asof_date, run_key]),
        )
