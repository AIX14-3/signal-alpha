from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.database import get_database_pool
from app.orchestrator.dart.corp_code_sync import DartCorpCodeSyncService
from app.orchestrator.queue.handlers import build_task_handlers
from app.orchestrator.queue.task_types import (
    AGGREGATE_SIGNAL,
    ANALYZE_DART,
    COLLECT_DART,
    META_COMBINE,
    ML_INFER,
    NORMALIZE_DART,
    RISK_VETO,
    SYNTHESIZE,
)
from app.orchestrator.queue.tasks import QueueTaskRunner, TaskHandler

router = APIRouter(prefix="/internal/dart", tags=["dart"])

CorpCodeSyncServiceFactory = Callable[[Any, Settings], DartCorpCodeSyncService]
DartTaskHandlerFactory = Callable[[Any], dict[str, TaskHandler]]


class DartE2ERunRequest(BaseModel):
    stock_id: int
    stock_code: str
    bgn_de: date
    end_de: date
    force_reprocess: bool = False
    priority: str = "batch"
    max_normalize_runs: int = Field(default=20, ge=0, le=100)
    max_analyze_runs: int = Field(default=20, ge=0, le=100)
    max_downstream_runs: int = Field(default=20, ge=0, le=100)
    run_until_idle: bool = False


def build_corp_code_sync_service(connection: Any, settings: Settings) -> DartCorpCodeSyncService:
    from signal_alpha_data_access.repositories import DartRepository

    from app.collectors.dart.corp_codes import DartCorpCodeClient

    return DartCorpCodeSyncService(
        client=DartCorpCodeClient(
            api_key=settings.dart_api_key,
            base_url=settings.dart_base_url,
            timeout_seconds=settings.dart_timeout_seconds,
        ),
        repository=DartRepository(connection),
    )


def get_corp_code_sync_service_factory() -> CorpCodeSyncServiceFactory:
    return build_corp_code_sync_service


def get_dart_task_handler_factory() -> DartTaskHandlerFactory:
    return build_task_handlers


@router.post("/corp-codes/sync")
async def sync_corp_codes(
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
    service_factory: CorpCodeSyncServiceFactory = Depends(get_corp_code_sync_service_factory),
) -> dict[str, int]:
    async with pool.acquire() as connection:
        service = service_factory(connection, settings)
        return await service.sync()


@router.get("/analysis-results")
async def list_analysis_results(
    stock_code: str,
    analysis_date: date | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import AnalysisRepository

    async with pool.acquire() as connection:
        rows = await AnalysisRepository(connection).list_dart_analysis_results(
            stock_code=stock_code,
            analysis_date=analysis_date,
            limit=limit,
        )
    items = [_analysis_result_item(row) for row in rows]
    return {"count": len(items), "items": items}


@router.get("/document-results")
async def list_document_results(
    stock_code: str,
    analysis_date: date | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import AnalysisRepository

    async with pool.acquire() as connection:
        rows = await AnalysisRepository(connection).list_dart_analysis_results(
            stock_code=stock_code,
            analysis_date=analysis_date,
            limit=limit,
        )
    items = _document_result_items(rows)
    return {"count": len(items), "items": items}


@router.delete("/test-data")
async def delete_test_data(
    stock_code: str,
    bgn_de: date,
    end_de: date,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import CollectionRepository

    async with pool.acquire() as connection:
        row = await CollectionRepository(connection).delete_dart_test_data(
            stock_code=stock_code,
            bgn_de=bgn_de,
            end_de=end_de,
        )
    return dict(row)


@router.post("/e2e/run")
async def run_e2e(
    request: DartE2ERunRequest,
    pool: Any = Depends(get_database_pool),
    handler_factory: DartTaskHandlerFactory = Depends(get_dart_task_handler_factory),
) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import AnalysisRepository, ProcessingQueueRepository

    task_context = {
        "stock_code": request.stock_code,
        "bgn_de": request.bgn_de.isoformat(),
        "end_de": request.end_de.isoformat(),
    }
    if request.force_reprocess:
        task_context["force_reprocess"] = True

    async with pool.acquire() as connection:
        collect_task_id = await ProcessingQueueRepository(connection).enqueue(
            stock_id=request.stock_id,
            task_type=COLLECT_DART,
            priority=request.priority,
            task_context=task_context,
            dedupe=False,
        )
        runner = QueueTaskRunner(connection, handler_factory(connection))
        collect_result = await runner.run_task(COLLECT_DART, task_id=collect_task_id)
        normalize_task_ids = _task_ids_from_result(collect_result, "queued_task_ids")
        normalize_results = await _run_task_ids(
            runner,
            NORMALIZE_DART,
            normalize_task_ids,
            request.max_normalize_runs,
            run_until_idle=request.run_until_idle,
        )
        analyze_task_ids = _task_ids_from_results(normalize_results, "analysis_task_ids")
        analyze_results = await _run_task_ids(
            runner,
            ANALYZE_DART,
            analyze_task_ids,
            request.max_analyze_runs,
            run_until_idle=request.run_until_idle,
        )
        ml_infer_task_ids = _task_ids_from_results(analyze_results, "ml_infer_task_id")
        ml_infer_results = await _run_task_ids(
            runner,
            ML_INFER,
            ml_infer_task_ids,
            request.max_downstream_runs,
            run_until_idle=request.run_until_idle,
        )
        meta_combine_task_ids = _task_ids_from_results(ml_infer_results, "meta_combine_task_id")
        meta_combine_results = await _run_task_ids(
            runner,
            META_COMBINE,
            meta_combine_task_ids,
            request.max_downstream_runs,
            run_until_idle=request.run_until_idle,
        )
        aggregate_task_ids = [
            *_task_ids_from_results(ml_infer_results, "aggregate_task_id"),
            *_task_ids_from_results(meta_combine_results, "aggregate_task_id"),
        ]
        aggregate_results = await _run_task_ids(
            runner,
            AGGREGATE_SIGNAL,
            aggregate_task_ids,
            request.max_downstream_runs,
            run_until_idle=request.run_until_idle,
        )
        synthesize_task_ids = _task_ids_from_results(aggregate_results, "synthesize_task_id")
        synthesize_results = await _run_task_ids(
            runner,
            SYNTHESIZE,
            synthesize_task_ids,
            request.max_downstream_runs,
            run_until_idle=request.run_until_idle,
        )
        risk_veto_task_ids = _task_ids_from_results(synthesize_results, "risk_veto_task_id")
        risk_veto_results = await _run_task_ids(
            runner,
            RISK_VETO,
            risk_veto_task_ids,
            request.max_downstream_runs,
            run_until_idle=request.run_until_idle,
        )
        analysis_rows = await AnalysisRepository(connection).list_dart_analysis_results(
            stock_code=request.stock_code,
            limit=20,
        )
        final_signal_rows = await AnalysisRepository(connection).list_final_signals_by_stock(
            stock_code=request.stock_code,
            bgn_de=request.bgn_de,
            end_de=request.end_de,
            run_key="AGGREGATED",
            limit=20,
        )

    items = [_analysis_result_item(row) for row in analysis_rows]
    final_signal_items = [_final_signal_item(row) for row in final_signal_rows]
    queue_summary = _queue_summary(
        normalize_task_ids=normalize_task_ids,
        normalize_results=normalize_results,
        analyze_task_ids=analyze_task_ids,
        analyze_results=analyze_results,
        ml_infer_task_ids=ml_infer_task_ids,
        ml_infer_results=ml_infer_results,
        meta_combine_task_ids=meta_combine_task_ids,
        meta_combine_results=meta_combine_results,
        aggregate_task_ids=aggregate_task_ids,
        aggregate_results=aggregate_results,
        synthesize_task_ids=synthesize_task_ids,
        synthesize_results=synthesize_results,
        risk_veto_task_ids=risk_veto_task_ids,
        risk_veto_results=risk_veto_results,
        max_normalize_runs=request.max_normalize_runs,
        max_analyze_runs=request.max_analyze_runs,
        max_downstream_runs=request.max_downstream_runs,
        run_until_idle=request.run_until_idle,
    )
    return {
        "collect_task_id": collect_task_id,
        "collect": collect_result,
        "normalize": normalize_results,
        "analyze": analyze_results,
        "ml_infer": ml_infer_results,
        "meta_combine": meta_combine_results,
        "aggregate_signal": aggregate_results,
        "synthesize": synthesize_results,
        "risk_veto": risk_veto_results,
        "queue_summary": queue_summary,
        "analysis_results": {"count": len(items), "items": items},
        "final_signals": {"count": len(final_signal_items), "items": final_signal_items},
    }


async def _run_task_ids(
    runner: QueueTaskRunner,
    task_type: str,
    task_ids: list[int],
    max_runs: int,
    *,
    run_until_idle: bool = False,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    task_limit = len(task_ids) if run_until_idle else max_runs
    for task_id in task_ids[:task_limit]:
        result = await runner.run_task(task_type, task_id=task_id)
        if result["status"] == "idle":
            continue
        results.append(result)
        if result["status"] == "failed":
            break
    return results


def _task_ids_from_results(results: list[dict[str, Any]], key: str) -> list[int]:
    task_ids: list[int] = []
    for result in results:
        task_ids.extend(_task_ids_from_result(result, key))
    return task_ids


def _task_ids_from_result(result: dict[str, Any], key: str) -> list[int]:
    payload = result.get("result") or {}
    value = payload.get(key) or []
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        return [int(value)]
    return [int(item) for item in value]


def _analysis_result_item(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["agent_results"] = _json_array(item.get("agent_results"))
    item["signal_events"] = _json_array(item.get("signal_events"))
    return item


def _final_signal_item(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["score_breakdown"] = _json_value(item.get("score_breakdown"))
    item["positive_evidence"] = _json_value(item.get("positive_evidence"))
    item["caution_evidence"] = _json_value(item.get("caution_evidence"))
    return item


def _document_result_items(rows: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        analysis_item = _analysis_result_item(row)
        agent_results = analysis_item["agent_results"]
        primary_agent = agent_results[0] if agent_results else {}
        method_detail = _method_detail(primary_agent)
        for event in analysis_item["signal_events"]:
            items.append(
                {
                    "analysis_result_id": analysis_item["id"],
                    "stock_id": analysis_item["stock_id"],
                    "stock_code": analysis_item["stock_code"],
                    "stock_name": analysis_item.get("stock_name"),
                    "analysis_date": analysis_item["analysis_date"],
                    "run_key": analysis_item["run_key"],
                    "analysis_mode": analysis_item.get("analysis_mode"),
                    "base_score": analysis_item.get("base_score"),
                    "warning": analysis_item.get("warning"),
                    "agent_results": agent_results,
                    "analysis_source": method_detail.get("analysis_source"),
                    "llm_model": primary_agent.get("llm_model"),
                    "prompt_ver": primary_agent.get("prompt_ver"),
                    "llm_confidence": method_detail.get("llm_confidence"),
                    "key_facts": method_detail.get("key_facts") or [],
                    "signal_event_id": event.get("id"),
                    "source_document_id": event.get("source_document_id"),
                    "event_type": event.get("event_type"),
                    "event_date": event.get("event_date"),
                    "signal_direction": event.get("signal_direction"),
                    "impact_level": event.get("impact_level"),
                    "title": event.get("title"),
                    "summary": event.get("summary"),
                    "evidence_url": event.get("evidence_url"),
                    "needs_review": event.get("needs_review"),
                }
            )
    return items


def _method_detail(agent_result: dict[str, Any]) -> dict[str, Any]:
    value = agent_result.get("method_detail")
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _queue_summary(
    *,
    normalize_task_ids: list[int],
    normalize_results: list[dict[str, Any]],
    analyze_task_ids: list[int],
    analyze_results: list[dict[str, Any]],
    ml_infer_task_ids: list[int],
    ml_infer_results: list[dict[str, Any]],
    meta_combine_task_ids: list[int],
    meta_combine_results: list[dict[str, Any]],
    aggregate_task_ids: list[int],
    aggregate_results: list[dict[str, Any]],
    synthesize_task_ids: list[int],
    synthesize_results: list[dict[str, Any]],
    risk_veto_task_ids: list[int],
    risk_veto_results: list[dict[str, Any]],
    max_normalize_runs: int,
    max_analyze_runs: int,
    max_downstream_runs: int,
    run_until_idle: bool,
) -> dict[str, Any]:
    normalize_run_count = len(normalize_results)
    analyze_run_count = len(analyze_results)
    normalize_pending_count = max(0, len(normalize_task_ids) - normalize_run_count)
    analyze_pending_count = max(0, len(analyze_task_ids) - analyze_run_count)
    return {
        "run_until_idle": run_until_idle,
        "normalize_total_count": len(normalize_task_ids),
        "normalize_run_count": normalize_run_count,
        "normalize_pending_count": normalize_pending_count,
        "normalize_limit_reached": not run_until_idle and len(normalize_task_ids) > max_normalize_runs,
        "analyze_total_count": len(analyze_task_ids),
        "analyze_run_count": analyze_run_count,
        "analyze_pending_count": analyze_pending_count,
        "analyze_limit_reached": not run_until_idle and len(analyze_task_ids) > max_analyze_runs,
        "ml_infer_total_count": len(ml_infer_task_ids),
        "ml_infer_run_count": len(ml_infer_results),
        "ml_infer_pending_count": max(0, len(ml_infer_task_ids) - len(ml_infer_results)),
        "ml_infer_limit_reached": not run_until_idle and len(ml_infer_task_ids) > max_downstream_runs,
        **_task_ids_summary(
            "meta_combine",
            meta_combine_task_ids,
            meta_combine_results,
            max_downstream_runs,
            run_until_idle,
        ),
        **_task_ids_summary(
            "aggregate_signal",
            aggregate_task_ids,
            aggregate_results,
            max_downstream_runs,
            run_until_idle,
        ),
        **_task_ids_summary(
            "synthesize",
            synthesize_task_ids,
            synthesize_results,
            max_downstream_runs,
            run_until_idle,
        ),
        **_task_ids_summary(
            "risk_veto",
            risk_veto_task_ids,
            risk_veto_results,
            max_downstream_runs,
            run_until_idle,
        ),
    }


def _task_ids_summary(
    prefix: str,
    task_ids: list[int],
    results: list[dict[str, Any]],
    max_runs: int,
    run_until_idle: bool,
) -> dict[str, Any]:
    run_count = len(results)
    return {
        f"{prefix}_total_count": len(task_ids),
        f"{prefix}_run_count": run_count,
        f"{prefix}_pending_count": max(0, len(task_ids) - run_count),
        f"{prefix}_limit_reached": not run_until_idle and len(task_ids) > max_runs,
    }


def _json_array(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return list(value)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value
