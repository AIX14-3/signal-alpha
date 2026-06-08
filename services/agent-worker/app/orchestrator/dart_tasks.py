from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.collectors.dart import DartCollector, DartDisclosureClient
from app.orchestrator.persistence import CollectionPersistence
from app.orchestrator.task_types import NORMALIZE_DART
from signal_alpha_data_access.repositories import DartRepository


class DartCollectionTaskHandler:
    def __init__(
        self,
        *,
        connection: Any,
        settings: Any,
        client: DartDisclosureClient | None = None,
    ) -> None:
        self._connection = connection
        self._settings = settings
        self._client = client

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        stock_code = _stock_code_from_context(task_context)

        collector = DartCollector(
            api_key=self._settings.dart_api_key,
            corp_code_repository=DartRepository(self._connection),
            client=self._client,
            start_date=task_context.get("bgn_de"),
            end_date=task_context.get("end_de"),
            page_size=self._settings.dart_page_size,
        )
        evidence = await collector.collect(stock_code)
        return await CollectionPersistence(self._connection).save_evidence_batch(
            stock_id=stock_id,
            stock_code=stock_code,
            evidence=evidence,
            collector_type="DART",
            enqueue_task_type=NORMALIZE_DART,
        )


def _stock_code_from_context(task_context: dict[str, Any]) -> str:
    stock_code = task_context.get("stock_code") or task_context.get("ticker")
    if not stock_code:
        raise ValueError("collect_dart task_context.stock_code is required.")
    return str(stock_code).strip()


def _task_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)
