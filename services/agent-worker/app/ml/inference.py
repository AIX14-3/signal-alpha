"""ML/DL 추론 단계 — architecture.mermaid의 "ML/DL 추론 (게이트 통과 모델만)".

``run_inference`` 는 하나의 ``DataContract`` 에 대해 게이트 통과+가용 모델들의 ``predict`` 를
호출해 신호 피처(pred_vol)를 만드는 순수 함수다(DB 미접근, 단위 테스트 용이). 개별 모델이
데이터 부족/백엔드 오류로 실패해도 파이프라인을 멈추지 않고 ``error`` 로 기록한다.

``MlInferTaskHandler`` 는 큐의 ``ML_INFER`` 핸들러: 종목 OHLCV(ohlcv_data)를 읽어
DataContract로 변환하고, 추론 결과를 ``ml_inferences`` 에 멱등 적재한다. (메타러너 결합은 PR3.)
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.ml.contract_adapter import build_contract
from app.ml.model_registry import ModelSpec, resolve_models
from vol_models.common.data_contract import DataContract

DEFAULT_HORIZON = 10
DEFAULT_LOOKBACK_SESSIONS = 400  # > harness warmup(375) so warmup-hungry models have history
DEFAULT_RUN_KEY = "ML"
DEFAULT_SEED = 42


@dataclass(frozen=True)
class InferenceResult:
    model_name: str
    device: str
    horizon: int
    pred_value: float | None
    error: str | None


def run_inference(
    contract: DataContract,
    *,
    horizon: int,
    specs: list[ModelSpec],
    seed: int = DEFAULT_SEED,
) -> list[InferenceResult]:
    """Run each spec's ``predict`` at the latest origin; isolate per-model failures."""
    rng = np.random.default_rng(seed)
    asof_idx = len(contract) - 1
    results: list[InferenceResult] = []
    for spec in specs:
        cfg = {**spec.cfg, "seed": seed}
        try:
            value = float(spec.load()(contract, asof_idx, horizon, cfg, rng))
            if not math.isfinite(value):
                raise ValueError(f"non-finite prediction: {value}")
            results.append(InferenceResult(spec.name, spec.device, horizon, value, None))
        except Exception as exc:  # noqa: BLE001 — record the gap, keep other models running
            results.append(InferenceResult(spec.name, spec.device, horizon, None, str(exc)))
    return results


class MlInferTaskHandler:
    """ML_INFER 큐 핸들러 — 종목 OHLCV → DataContract → 모델 추론 → ml_inferences 적재."""

    def __init__(
        self,
        connection: Any,
        *,
        horizon: int | None = None,
        lookback_sessions: int | None = None,
        run_key: str | None = None,
        seed: int | None = None,
    ) -> None:
        from signal_alpha_data_access.repositories import (
            MarketDataRepository,
            MlInferenceRepository,
        )

        self._connection = connection
        self._market_data = MarketDataRepository(connection)
        self._inferences = MlInferenceRepository(connection)
        self._horizon = horizon if horizon is not None else _int_env("ML_HORIZON", DEFAULT_HORIZON)
        self._lookback = (
            lookback_sessions
            if lookback_sessions is not None
            else _int_env("ML_LOOKBACK_SESSIONS", DEFAULT_LOOKBACK_SESSIONS)
        )
        self._run_key = run_key or os.getenv("ML_RUN_KEY") or DEFAULT_RUN_KEY
        self._seed = seed if seed is not None else _int_env("ML_SEED", DEFAULT_SEED)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        stock_code = str(task_context.get("stock_code") or task_context.get("ticker") or "")

        records = await self._market_data.list_recent_ohlcv(
            stock_id=stock_id, limit=self._lookback
        )
        rows = [dict(record) for record in records]
        if not rows:
            return {
                "stock_id": stock_id,
                "asof_date": None,
                "model_count": 0,
                "persisted": 0,
                "skipped_reason": "no_ohlcv",
            }

        contract = build_contract(rows, ticker=stock_code or str(stock_id))
        asof_date = contract.frame["date"].iloc[-1].date()
        specs = resolve_models()
        results = run_inference(
            contract, horizon=self._horizon, specs=specs, seed=self._seed
        )

        for result in results:
            await self._inferences.upsert_inference(
                stock_id=stock_id,
                run_key=self._run_key,
                asof_date=asof_date,
                model_name=result.model_name,
                horizon=result.horizon,
                pred_value=result.pred_value,
                device=result.device,
                gate_passed=True,
                error_message=result.error,
            )

        return {
            "stock_id": stock_id,
            "asof_date": asof_date.isoformat(),
            "run_key": self._run_key,
            "horizon": self._horizon,
            "model_count": len(results),
            "persisted": len(results),
            "succeeded": [r.model_name for r in results if r.error is None],
            "failed": {r.model_name: r.error for r in results if r.error is not None},
        }


def _task_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default
