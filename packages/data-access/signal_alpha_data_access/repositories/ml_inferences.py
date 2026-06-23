from __future__ import annotations

from typing import Any


class MlInferenceRepository:
    """ml_inferences 적재/조회 — vol-benchmark 모델 추론 원장.

    모델×horizon×asof 당 1행을 자연키(stock_id, run_key, asof_date, model_name,
    horizon)로 멱등 upsert 한다. 메타러너(PR3)는 ``list_for_run`` 으로 한 종목/asof의
    추론 피처를 읽어 stacking 입력으로 쓴다.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_inference(
        self,
        *,
        stock_id: int,
        run_key: str,
        asof_date: Any,
        model_name: str,
        horizon: int,
        pred_value: float | None,
        device: str = "cpu",
        gate_passed: bool = True,
        error_message: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO ml_inferences (
                stock_id, run_key, asof_date, model_name, horizon,
                pred_value, device, gate_passed, error_message
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (stock_id, run_key, asof_date, model_name, horizon)
            DO UPDATE SET
                pred_value = EXCLUDED.pred_value,
                device = EXCLUDED.device,
                gate_passed = EXCLUDED.gate_passed,
                error_message = EXCLUDED.error_message,
                created_at = NOW()
            RETURNING *
            """,
            stock_id,
            run_key,
            asof_date,
            model_name,
            horizon,
            pred_value,
            device,
            gate_passed,
            error_message,
        )

    async def list_for_run(
        self,
        *,
        stock_id: int,
        run_key: str,
        asof_date: Any,
        horizon: int | None = None,
    ) -> list[Any]:
        """한 종목/asof의 추론 행 — 메타러너 stacking 입력. horizon 미지정 시 전체."""
        if horizon is None:
            return await self._connection.fetch(
                """
                SELECT *
                FROM ml_inferences
                WHERE stock_id = $1 AND run_key = $2 AND asof_date = $3
                ORDER BY model_name ASC, horizon ASC
                """,
                stock_id,
                run_key,
                asof_date,
            )
        return await self._connection.fetch(
            """
            SELECT *
            FROM ml_inferences
            WHERE stock_id = $1 AND run_key = $2 AND asof_date = $3 AND horizon = $4
            ORDER BY model_name ASC
            """,
            stock_id,
            run_key,
            asof_date,
            horizon,
        )
