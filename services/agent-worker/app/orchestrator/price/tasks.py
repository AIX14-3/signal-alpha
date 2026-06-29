"""ANALYZE_PRICE queue handler.

PRICE was the one report source with no queue task — its analyzer existed but was
only reachable from the in-memory ``run_pipeline.py`` harness, so the cross-source
aggregation never saw a PRICE ``analysis_result`` and the report card stayed
``missing``. This handler makes PRICE a first-class queue peer of DART/ALTERNATIVE:
read ``ohlcv_data`` back via ``OhlcvReader`` → ``PriceAnalyzer`` → persist one
``analysis_result`` + ``agent_result`` under run_key ``PRICE`` so the fan-in
``AGGREGATE_SIGNAL`` blends it in. PRICE has no ``signal_events`` (the OHLCV series
rides in ``RawEvidence.metadata``), so ``source_signal_event_ids`` is empty.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from app.analyzers.price.analyzer import PriceAnalyzer
from app.collectors.price.ohlcv_reader import OhlcvReader
from app.orchestrator.queue.context import enqueue_aggregate
from app.orchestrator.queue.task_types import SRC_INFER
from signal_alpha_data_access.repositories import (
    AnalysisRepository,
    MarketDataRepository,
    ProcessingQueueRepository,
    StockRepository,
)

PRICE_RUN_KEY = "PRICE"
# agent_results.debate_method CHECK allows D-1..D-5. DART/HIRING=D-1, PATENT=D-2,
# DATALAB=D-3; PRICE takes the next free code so its agent_result never collides
# with another source on the (result_id, debate_method) unique key.
PRICE_DEBATE_METHOD = "D-4"
PRICE_VERSION = "price-rules-v1"


class PriceAnalyzeTaskHandler:
    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._analysis_repository = AnalysisRepository(connection)
        self._reader = OhlcvReader(
            stocks=StockRepository(connection),
            market_data=MarketDataRepository(connection),
        )
        self._queue = ProcessingQueueRepository(connection)
        self._analyzer = PriceAnalyzer()

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        stock_code = str(task_context.get("stock_code") or "")
        analysis_date = _to_date(
            task_context.get("signal_date") or task_context.get("analysis_date")
        )

        evidence = await self._reader.collect(stock_code)
        result = await self._analyzer.analyze(stock_code, evidence)

        analysis_result = await self._analysis_repository.upsert_analysis_result(
            stock_id=stock_id,
            analysis_date=analysis_date,
            run_key=PRICE_RUN_KEY,
            source_signal_event_ids=[],
            base_score=_to_db_score(result.score),
            # analysis_mode 는 DB CHECK(analysis_results_analysis_mode_check) 상 {full,dart_only,quick}
            # 만 허용한다 — REPORT/ALTERNATIVE 와 동일하게 "full" 사용(소스 식별은 run_key="PRICE" +
            # method_detail.source="PRICE" 로 하므로 분석모드 값에 의존하지 않는다). 'price_only' 는
            # 제약 위반이라 적재 자체가 실패했었다(Neon→GCP 덤프 배포에서 마이그레이션 없이 안전).
            analysis_mode="full",
            warning="; ".join(result.risk_flags) or None,
            version=PRICE_VERSION,
        )
        agent_result = await self._analysis_repository.upsert_agent_result(
            result_id=int(analysis_result["id"]),
            stock_id=stock_id,
            debate_method=PRICE_DEBATE_METHOD,
            source_signal_event_ids=[],
            method_score=_to_db_score(result.score),
            method_signal=result.direction,
            method_detail={
                "source": "PRICE",
                "source_score": result.score,
                "summary": result.summary,
                "risk_flags": result.risk_flags,
                "data_status": result.data_status,
            },
            reliability_score=80,
            evidence_quality=100 if result.data_status == "ok" else 0,
            prompt_ver=PRICE_VERSION,
        )
        # 메타러너 SRC 라인 트리거(C안): 주가 BASE 앵커 ⊕ 대체데이터 → 소스별 7예측률.
        # 주가는 매 종목마다 분석되므로 여기서 per-stock 1회 SRC_INFER 를 인큐한다. SRC_INFER 가
        # base 모델로 추론(아티팩트 있는 소스만, 현재 src_price) → RETURN_COMBINE → meta_signals/
        # final_signals.source_predictions. 아티팩트 전무면 예측 None 으로 안전 no-op(점수 불변).
        priority = str(task_context.get("priority") or "batch")
        src_infer_task_id = await self._queue.enqueue(
            stock_id=stock_id,
            task_type=SRC_INFER,
            priority=priority,
            task_context={"stock_code": stock_code, "as_of": analysis_date.isoformat()},
            dedupe=True,
        )
        # 주가는 평일 매일 분석되므로 = 발행 하한선. 대체데이터가 그날 없는 단독 종목도 발행되도록
        # 여기서 AGGREGATE_SIGNAL 을 인큐한다(fan-in: (stock_id, signal_date)로 모든 소스를 모음).
        # 대체데이터가 같은 날 AGGREGATE 를 인큐해도 dedupe 로 한 번만 집계된다.
        aggregate_task_id = await enqueue_aggregate(
            self._queue,
            stock_id=stock_id,
            aggregate_ctx={
                "signal_date": analysis_date.isoformat(),
                "stock_code": stock_code,
            },
            priority=priority,
        )
        return {
            "analysis_result_id": int(analysis_result["id"]),
            "agent_result_id": int(agent_result["id"]),
            "direction": result.direction,
            "score": result.score,
            "data_status": result.data_status,
            "src_infer_task_id": src_infer_task_id,
            "aggregate_task_id": aggregate_task_id,
        }


def _task_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            pass
    return date.today()


def _to_db_score(score: float) -> float:
    """Map the signed [-1, +1] source score onto the legacy DB 0-100 columns."""
    return round(max(0.0, min(100.0, (score + 1.0) * 50.0)), 2)
