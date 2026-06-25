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
from signal_alpha_data_access.repositories import (
    AnalysisRepository,
    MarketDataRepository,
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
            analysis_mode="price_only",
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
        return {
            "analysis_result_id": int(analysis_result["id"]),
            "agent_result_id": int(agent_result["id"]),
            "direction": result.direction,
            "score": result.score,
            "data_status": result.data_status,
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
