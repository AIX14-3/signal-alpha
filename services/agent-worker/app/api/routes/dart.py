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
from app.orchestrator.queue.task_types import ANALYZE_DART, COLLECT_DART, NORMALIZE_DART
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
        )
        analyze_task_ids = _task_ids_from_results(normalize_results, "analysis_task_ids")
        analyze_results = await _run_task_ids(
            runner,
            ANALYZE_DART,
            analyze_task_ids,
            request.max_analyze_runs,
        )
        analysis_rows = await AnalysisRepository(connection).list_dart_analysis_results(
            stock_code=request.stock_code,
            limit=20,
        )

    items = [_analysis_result_item(row) for row in analysis_rows]
    return {
        "collect_task_id": collect_task_id,
        "collect": collect_result,
        "normalize": normalize_results,
        "analyze": analyze_results,
        "analysis_results": {"count": len(items), "items": items},
    }


async def _run_task_ids(
    runner: QueueTaskRunner,
    task_type: str,
    task_ids: list[int],
    max_runs: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for task_id in task_ids[:max_runs]:
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
    return [int(item) for item in value]


def _analysis_result_item(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["agent_results"] = _json_array(item.get("agent_results"))
    item["signal_events"] = _json_array(item.get("signal_events"))
    return item


def _json_array(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return list(value)
