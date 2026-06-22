"""META_COMBINE 큐 핸들러 — 메타러너 stacking 결합 단계.

ML_INFER가 적재한 ml_inferences(모델별 pred_vol)를 읽어 ``meta_learner.combine`` 으로
하나의 결합 변동성 + 신뢰도로 합치고 meta_signals에 멱등 적재한다. ML_INFER가 성공 추론이
있을 때 이 태스크를 enqueue 한다(task_context: run_key, asof_date, horizon).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.ml.inference import DEFAULT_HORIZON, DEFAULT_RUN_KEY
from app.ml.meta_learner import combine, load_weights
from app.orchestrator.queue.context import parse_task_context


class MetaCombineTaskHandler:
    def __init__(self, connection: Any) -> None:
        from signal_alpha_data_access.repositories import (
            MetaSignalRepository,
            MlInferenceRepository,
        )

        self._inferences = MlInferenceRepository(connection)
        self._meta = MetaSignalRepository(connection)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        ctx = parse_task_context(task.get("task_context"))
        run_key = str(ctx.get("run_key") or DEFAULT_RUN_KEY)
        asof_date = ctx.get("asof_date")
        horizon = int(ctx.get("horizon") or DEFAULT_HORIZON)
        if not asof_date:
            return {"stock_id": stock_id, "skipped_reason": "asof_date_required"}

        rows = [
            dict(row)
            for row in await self._inferences.list_for_run(
                stock_id=stock_id, run_key=run_key, asof_date=asof_date, horizon=horizon
            )
        ]
        predictions = {
            str(row["model_name"]): float(row["pred_value"])
            for row in rows
            if row.get("pred_value") is not None and bool(row.get("gate_passed", True))
        }

        result = combine(predictions, weights=load_weights())
        await self._meta.upsert_meta_signal(
            stock_id=stock_id,
            run_key=run_key,
            asof_date=asof_date,
            horizon=horizon,
            combined_vol=result.combined_vol,
            confidence=result.confidence,
            method=result.method,
            model_count=result.model_count,
            weight_breakdown=result.weight_breakdown,
        )
        return {
            "stock_id": stock_id,
            "run_key": run_key,
            "asof_date": asof_date,
            "horizon": horizon,
            "combined_vol": result.combined_vol,
            "confidence": result.confidence,
            "method": result.method,
            "model_count": result.model_count,
        }
