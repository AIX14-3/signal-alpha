from __future__ import annotations

import json
from typing import Any


class MetaSignalRepository:
    """meta_signals 적재/조회 — 메타러너(stacking) 결합 결과 원장.

    종목/run_key/asof/horizon 당 1행을 자연키로 멱등 upsert 한다. 끝단 LLM 종합(PR5)/리포트가
    ``get`` 으로 결합 변동성·신뢰도를 읽는다.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_meta_signal(
        self,
        *,
        stock_id: int,
        run_key: str,
        asof_date: Any,
        horizon: int,
        combined_vol: float | None,
        confidence: float,
        method: str,
        model_count: int,
        weight_breakdown: dict[str, float] | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO meta_signals (
                stock_id, run_key, asof_date, horizon,
                combined_vol, confidence, method, model_count, weight_breakdown
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (stock_id, run_key, asof_date, horizon)
            DO UPDATE SET
                combined_vol = EXCLUDED.combined_vol,
                confidence = EXCLUDED.confidence,
                method = EXCLUDED.method,
                model_count = EXCLUDED.model_count,
                weight_breakdown = EXCLUDED.weight_breakdown,
                created_at = NOW()
            RETURNING *
            """,
            stock_id,
            run_key,
            asof_date,
            horizon,
            combined_vol,
            confidence,
            method,
            model_count,
            json.dumps(weight_breakdown or {}),
        )

    async def get(
        self,
        *,
        stock_id: int,
        run_key: str,
        asof_date: Any,
        horizon: int,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM meta_signals
            WHERE stock_id = $1 AND run_key = $2 AND asof_date = $3 AND horizon = $4
            """,
            stock_id,
            run_key,
            asof_date,
            horizon,
        )
