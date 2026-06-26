"""RETURN_COMBINE 큐 핸들러 — 메타러너 return 채널 결합 (#525 WS-C).

vol 채널의 ``MetaCombineTaskHandler`` 와 짝을 이루는 return 채널 판본. SRC_INFER 가 적재한
``ml_inferences``(run_key='SRC', model_name=src_*, forward-return 예측)를 읽고, 저빈도 Report
피처(D1)를 PIT 어셈블해 ``combine_return`` 으로 결합한 뒤, ``meta_signals``(run_key='SRC')의
return 컬럼(final_score/direction/confidence)에 멱등 적재한다.

경계(D4): combined_vol 은 건드리지 않는다(이 행은 NULL). vol 채널(run_key='ML')과 자연키로 공존.
SRC_INFER 가 성공 예측이 있을 때 이 태스크를 enqueue 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from app.ml.meta_learner import combine_return, load_return_model
from app.ml.source_features import assemble_features
from app.ml.source_inference import DEFAULT_HORIZON
from app.ml.source_models import SOURCE_MODELS, SOURCE_RUN_KEY
from app.orchestrator.queue.context import parse_task_context


class ReturnCombineTaskHandler:
    def __init__(
        self,
        connection: Any,
        *,
        inferences: Any | None = None,
        meta: Any | None = None,
        collection: Any | None = None,
        analysis: Any | None = None,
        run_key: str = SOURCE_RUN_KEY,
        return_model: Mapping | None = None,
    ) -> None:
        if inferences is None or meta is None or collection is None or analysis is None:
            from signal_alpha_data_access.repositories import (
                AnalysisRepository,
                CollectionRepository,
                MetaSignalRepository,
                MlInferenceRepository,
            )

            inferences = inferences or MlInferenceRepository(connection)
            meta = meta or MetaSignalRepository(connection)
            collection = collection or CollectionRepository(connection)
            analysis = analysis or AnalysisRepository(connection)

        self._inferences = inferences
        self._meta = meta
        self._collection = collection
        self._analysis = analysis
        self._run_key = run_key
        # 학습 산출물은 핸들러 수명 동안 1회 로드(없으면 base 예측 균등 폴백).
        self._return_model = return_model if return_model is not None else load_return_model()

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        ctx = parse_task_context(task.get("task_context"))
        as_of = _to_date(ctx.get("as_of") or ctx.get("asof_date"))
        horizon = int(ctx.get("horizon") or DEFAULT_HORIZON)
        if as_of is None:
            return {"stock_id": stock_id, "skipped_reason": "asof_date_required"}

        rows = [
            dict(row)
            for row in await self._inferences.list_for_run(
                stock_id=stock_id, run_key=self._run_key, asof_date=as_of, horizon=horizon
            )
        ]
        base_predictions = {
            str(row["model_name"]): float(row["pred_value"])
            for row in rows
            if row.get("pred_value") is not None
            and bool(row.get("gate_passed", True))
            and str(row["model_name"]) in SOURCE_MODELS
        }

        report_features = await self._report_features(stock_id, as_of)
        result = combine_return(
            base_predictions,
            report_features=report_features,
            model=self._return_model,
        )

        await self._meta.upsert_meta_signal(
            stock_id=stock_id,
            run_key=self._run_key,
            asof_date=as_of,
            horizon=horizon,
            combined_vol=None,  # return 채널 행 — vol 채널 불변(D4)
            confidence=result.confidence,
            method=result.method,
            model_count=result.model_count,
            weight_breakdown=result.weight_breakdown,
            final_score=result.final_score,
            direction=result.direction,
        )
        # 소비처(백엔드/프론트) 노출: 현재 발행 신호에 return 채널을 오버레이(api.signals_current
        # 가 final_signals.* 를 노출하므로 자동 전파). 신호 미생성 시 no-op(다음 사이클에 채움).
        overlaid = await self._analysis.update_final_signal_return_channel(
            stock_id=stock_id,
            ml_final_score=result.final_score,
            ml_direction=result.direction,
            ml_confidence=result.confidence,
        )
        return {
            "stock_id": stock_id,
            "run_key": self._run_key,
            "asof_date": as_of.isoformat(),
            "horizon": horizon,
            "final_score": result.final_score,
            "direction": result.direction,
            "confidence": result.confidence,
            "method": result.method,
            "model_count": result.model_count,
            "final_signal_overlaid": overlaid is not None,
        }

    async def _report_features(self, stock_id: int, as_of: date) -> dict | None:
        """Report 정형 피처(D1: base 모델 없이 메타러너 피처 직접). PIT 어셈블."""
        facts = [
            dict(row)
            for row in await self._collection.list_report_valuation_facts(stock_id=stock_id)
        ]
        if not facts:
            return None
        return assemble_features(as_of, report_facts=facts)["report"]


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
