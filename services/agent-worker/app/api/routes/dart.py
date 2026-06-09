from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends

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
