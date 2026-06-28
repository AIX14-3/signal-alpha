"""SRC_INFER 큐 핸들러 — 소스별 base 모델 추론 단계 (#525 Phase 3 배선).

``MlInferTaskHandler`` 의 소스-피처 판본: 종목의 DataLab/Hiring 정형 행을 기존 evidence
로더로 읽어 PIT 피처로 어셈블(``assemble_features``)하고, 학습된 base 모델
(``SourceModel``)로 **forward return** 을 예측해 ``ml_inferences`` 에 적재한다.

기존 vol 추론(run_key=ML)과의 차이/경계:
- 입력이 OHLCV ``DataContract`` 가 아니라 소스 정형 피처(임베딩 아님).
- ``model_name=src_datalab/src_hiring``, ``run_key=SOURCE_RUN_KEY("SRC")`` — 타깃이 return 이라
  vol 등가평균 결합(run_key=ML)에 섞이면 ``combined_vol`` 오염(D4). 그래서 run_key 로 분리 적재만 하고
  여기서 META_COMBINE 을 인큐하지 않는다. **return 채널 결합은 WS-C** 가 run_key=SRC 를 읽어 수행.
- 아티팩트/lightgbm 부재 시 예측은 None → 결측으로 적재(gate_passed=False, error=model_unavailable).
  결정론 폴백 없음(D2); 메타러너 가용성 재정규화가 결측을 흡수.

로더·모델·repo 는 주입 가능(테스트는 DB 없이 fake 로 검증).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.ml.source_features import DEFAULT_LOOKBACK_DAYS
from app.ml.source_models import SOURCE_MODELS, SOURCE_RUN_KEY, SourceModel, predict_sources
from app.orchestrator.queue.context import parse_task_context
from app.orchestrator.queue.task_types import RETURN_COMBINE

# return base 모델의 공통 타깃 호라이즌(L6 abnormal_return_20d 와 정렬).
DEFAULT_HORIZON = 20
# DB 조회 윈도우(일) — 피처 recent/prior 윈도우를 모두 덮도록 피처 lookback 보다 길게.
DEFAULT_LOADER_LOOKBACK_DAYS = 60
# 주가 BASE 피처용 OHLCV 세션 수 — sma60 + 여유. PIT 게이트가 as_of 초과 행을 제거한다.
DEFAULT_PRICE_LOOKBACK_SESSIONS = 80
# 학습된 base 모델 아티팩트 디렉터리(파일명 = f"{model_name}.txt", LightGBM Booster 저장형식).
DEFAULT_ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts" / "source_models"


def load_source_models(artifact_dir: str | Path) -> list[SourceModel]:
    """``SOURCE_MODELS`` 각각을 아티팩트에서 적재. 파일 부재면 미적재(예측 None)."""
    directory = Path(artifact_dir)
    models: list[SourceModel] = []
    for model_name in SOURCE_MODELS:
        model = SourceModel(model_name)
        artifact = directory / f"{model_name}.txt"
        if artifact.exists():
            model.load(str(artifact))
        models.append(model)
    return models


class SrcInferTaskHandler:
    """SRC_INFER 핸들러 — 소스 정형 피처 → base 모델 → ml_inferences(src_*, run_key=SRC)."""

    def __init__(
        self,
        connection: Any,
        *,
        datalab_loader: Any | None = None,
        hiring_loader: Any | None = None,
        dart_loader: Any | None = None,
        market_data: Any | None = None,
        models: list[SourceModel] | None = None,
        inferences: Any | None = None,
        queue: Any | None = None,
        loader_lookback_days: int | None = None,
        feature_lookback_days: int | None = None,
        price_lookback_sessions: int | None = None,
        run_key: str = SOURCE_RUN_KEY,
        horizon: int | None = None,
        artifact_dir: str | Path | None = None,
    ) -> None:
        self._connection = connection
        self._run_key = run_key
        self._horizon = horizon if horizon is not None else _int_env("SRC_HORIZON", DEFAULT_HORIZON)
        # 주가 BASE 피처용 OHLCV 조회 세션 수(sma60 + 버퍼). PIT 게이트가 as_of 초과분을 잘라낸다.
        self._price_lookback = (
            price_lookback_sessions
            if price_lookback_sessions is not None
            else _int_env("SRC_PRICE_LOOKBACK_SESSIONS", DEFAULT_PRICE_LOOKBACK_SESSIONS)
        )
        self._feature_lookback = (
            feature_lookback_days
            if feature_lookback_days is not None
            else _int_env("SRC_FEATURE_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)
        )
        loader_lookback = (
            loader_lookback_days
            if loader_lookback_days is not None
            else _int_env("SRC_LOADER_LOOKBACK_DAYS", DEFAULT_LOADER_LOOKBACK_DAYS)
        )

        # 로더/리포지토리: 주입 없으면 실제 RawDetailRepository 기반으로 구성.
        if (
            datalab_loader is None
            or hiring_loader is None
            or dart_loader is None
            or market_data is None
            or inferences is None
            or queue is None
        ):
            from signal_alpha_data_access.repositories import (
                MarketDataRepository,
                MlInferenceRepository,
                ProcessingQueueRepository,
                RawDetailRepository,
            )

            repo = RawDetailRepository(connection)
            if market_data is None:
                market_data = MarketDataRepository(connection)
            if datalab_loader is None:
                from app.evidence_loaders.datalab_loader import DataLabEvidenceLoader

                datalab_loader = DataLabEvidenceLoader(repo, lookback_days=loader_lookback)
            if hiring_loader is None:
                from app.evidence_loaders.hiring_loader import HiringEvidenceLoader

                hiring_loader = HiringEvidenceLoader(repo, lookback_days=loader_lookback)
            if dart_loader is None:
                from app.evidence_loaders.dart_loader import DartEvidenceLoader

                dart_loader = DartEvidenceLoader(repo, lookback_days=loader_lookback)
            if inferences is None:
                inferences = MlInferenceRepository(connection)
            if queue is None:
                queue = ProcessingQueueRepository(connection)

        self._datalab_loader = datalab_loader
        self._hiring_loader = hiring_loader
        self._dart_loader = dart_loader
        self._market_data = market_data
        self._inferences = inferences
        self._queue = queue
        self._models = (
            models
            if models is not None
            else load_source_models(
                artifact_dir or os.getenv("SRC_MODEL_DIR") or DEFAULT_ARTIFACT_DIR
            )
        )

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        ctx = parse_task_context(task.get("task_context"))
        stock_code = str(ctx.get("stock_code") or ctx.get("ticker") or "")
        as_of = _to_date(ctx.get("as_of") or ctx.get("asof_date")) or date.today()

        datalab_rows = await self._rows(self._datalab_loader, stock_id, stock_code, as_of)
        hiring_rows, sector_demand = await self._hiring(stock_id, stock_code, as_of)
        dart_rows = await self._rows(self._dart_loader, stock_id, stock_code, as_of)
        price_rows = await self._price_rows(stock_id)

        predictions = predict_sources(
            as_of,
            models=self._models,
            datalab_rows=datalab_rows,
            hiring_rows=hiring_rows,
            dart_rows=dart_rows,
            price_rows=price_rows,
            lookback_days=self._feature_lookback,
            sector_demand=sector_demand,
        )

        for model_name, value in predictions.items():
            await self._inferences.upsert_inference(
                stock_id=stock_id,
                run_key=self._run_key,
                asof_date=as_of,
                model_name=model_name,
                horizon=self._horizon,
                pred_value=value,
                device="cpu",
                gate_passed=value is not None,
                error_message=None if value is not None else "model_unavailable",
            )

        succeeded = [name for name, value in predictions.items() if value is not None]
        # 성공 예측이 있으면 return 채널 결합(RETURN_COMBINE)을 인큐 — run_key=SRC 의 src_*
        # 예측 + Report 피처를 결합해 meta_signals return 컬럼에 적재한다(vol 채널 불변, D4).
        return_combine_task_id: int | None = None
        if succeeded:
            return_combine_task_id = await self._queue.enqueue(
                stock_id=stock_id,
                task_type=RETURN_COMBINE,
                priority=str(ctx.get("priority") or "batch"),
                task_context={
                    "stock_code": stock_code,
                    "run_key": self._run_key,
                    "as_of": as_of.isoformat(),
                    "horizon": self._horizon,
                },
                dedupe=True,
            )
        return {
            "stock_id": stock_id,
            "as_of": as_of.isoformat(),
            "run_key": self._run_key,
            "horizon": self._horizon,
            "predictions": dict(predictions),
            "persisted": len(predictions),
            "succeeded": succeeded,
            "return_combine_task_id": return_combine_task_id,
        }

    async def _price_rows(self, stock_id: int) -> list[dict]:
        """주가 BASE 피처용 최근 OHLCV(체결일·종가·거래량·외국인/기관 순매수). 어셈블러가
        ``trade_date <= as_of`` PIT 게이트를 적용하므로 최근 세션을 넉넉히 읽어도 안전하다."""
        records = await self._market_data.list_recent_ohlcv(
            stock_id=stock_id, limit=self._price_lookback
        )
        return [dict(record) for record in records]

    async def _rows(self, loader: Any, stock_id: int, stock_code: str, as_of: date) -> list[dict]:
        evidence = await loader.load(stock_id=stock_id, stock_code=stock_code, as_of=as_of)
        if not evidence:
            return []
        return list(evidence[0].metadata.get("rows") or [])

    async def _hiring(
        self, stock_id: int, stock_code: str, as_of: date
    ) -> tuple[list[dict], dict | None]:
        evidence = await self._hiring_loader.load(
            stock_id=stock_id, stock_code=stock_code, as_of=as_of
        )
        if not evidence:
            return [], None
        metadata = evidence[0].metadata
        return list(metadata.get("rows") or []), metadata.get("sector_demand")


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


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
