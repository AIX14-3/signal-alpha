from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.config import Settings, get_settings
from app.core.database import get_database_pool
from app.orchestrator.dart.corp_code_sync import DartCorpCodeSyncService

router = APIRouter(prefix="/internal/dart", tags=["dart"])

CorpCodeSyncServiceFactory = Callable[[Any, Settings], DartCorpCodeSyncService]


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
