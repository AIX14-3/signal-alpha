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

# 주가 BASE 앵커 모델. 모든 소스별 예측률은 이 값을 앵커로 포함해 융합한다(C안).
PRICE_MODEL = "src_price"
# 소스별 융합 결과(주가 BASE ⊕ 소스) run_key. 통합은 SOURCE_RUN_KEY("SRC").
PER_SOURCE_RUN_KEY: dict[str, str] = {
    "src_price": "SRC_PRICE",
    "src_datalab": "SRC_DATALAB",
    "src_hiring": "SRC_HIRING",
    "src_dart": "SRC_DART",
    "src_patent": "SRC_PATENT",
}
# Report 는 base 모델 없이 메타러너 피처(D1) → 주가 ⊕ Report 피처로 융합.
REPORT_RUN_KEY = "SRC_REPORT"


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
        price_pred = base_predictions.get(PRICE_MODEL)

        # ── 소스별 예측률(주가 BASE ⊕ 소스) ── 주가가 항상 앵커로 포함된다(C안). 의미 없는
        # (예측 None) 결과는 적재하지 않는다. 주가 단독은 6개 중 하나로 직접 노출.
        per_source: list[tuple[str, Any]] = []
        if price_pred is not None:
            per_source.append(
                (PER_SOURCE_RUN_KEY[PRICE_MODEL], combine_return({PRICE_MODEL: price_pred}, model=self._return_model))
            )
        for model_name, run_key in PER_SOURCE_RUN_KEY.items():
            if model_name == PRICE_MODEL:
                continue
            src_pred = base_predictions.get(model_name)
            if src_pred is None:
                continue
            fused = self._fuse_with_price(price_pred, {model_name: src_pred})
            if fused.final_score is not None:
                per_source.append((run_key, fused))
        if report_features:
            report_fused = self._fuse_with_price(price_pred, {}, report_features=report_features)
            if report_fused.final_score is not None:
                per_source.append((REPORT_RUN_KEY, report_fused))

        # ── 통합 예측률 = 전 소스(주가 포함) + Report 피처 결합 ──
        integrated = combine_return(
            base_predictions, report_features=report_features, model=self._return_model
        )

        # 소스별 6개 + 통합 1개를 meta_signals(per-source run_key)로 멱등 적재(combined_vol=NULL, D4).
        for run_key, res in [*per_source, (self._run_key, integrated)]:
            await self._meta.upsert_meta_signal(
                stock_id=stock_id,
                run_key=run_key,
                asof_date=as_of,
                horizon=horizon,
                combined_vol=None,
                confidence=res.confidence,
                method=res.method,
                model_count=res.model_count,
                weight_breakdown=res.weight_breakdown,
                final_score=res.final_score,
                direction=res.direction,
            )
        # 발행 신호 오버레이: ml_*(통합) + source_predictions(소스별 6 + 통합 1 = 7개)를
        # final_signals 에 기록 → publisher(SELECT *)·api.signals_current(final_signals.*) 자동 전파(P3).
        source_predictions = {
            run_key: {
                "final_score": res.final_score,
                "direction": res.direction,
                "confidence": res.confidence,
                "model_count": res.model_count,
            }
            for run_key, res in [*per_source, (self._run_key, integrated)]
        }
        overlaid = await self._analysis.update_final_signal_return_channel(
            stock_id=stock_id,
            ml_final_score=integrated.final_score,
            ml_direction=integrated.direction,
            ml_confidence=integrated.confidence,
            source_predictions=source_predictions,
        )
        return {
            "stock_id": stock_id,
            "run_key": self._run_key,
            "asof_date": as_of.isoformat(),
            "horizon": horizon,
            "final_score": integrated.final_score,
            "direction": integrated.direction,
            "confidence": integrated.confidence,
            "method": integrated.method,
            "model_count": integrated.model_count,
            "per_source": {run_key: res.final_score for run_key, res in per_source},
            "final_signal_overlaid": overlaid is not None,
        }

    def _fuse_with_price(
        self,
        price_pred: float | None,
        extra: Mapping[str, float],
        *,
        report_features: Mapping[str, float | None] | None = None,
    ) -> Any:
        """주가 BASE 를 앵커로 포함해 소스 예측/피처와 융합. 주가 부재 시 소스만으로 강등(graceful)."""
        inputs: dict[str, float] = {}
        if price_pred is not None:
            inputs[PRICE_MODEL] = price_pred
        inputs.update(extra)
        return combine_return(inputs, report_features=report_features, model=self._return_model)

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
