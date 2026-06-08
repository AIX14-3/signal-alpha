from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from app.collectors.dart import DartCollector, DartDisclosureClient
from app.orchestrator.dart_normalizer import classify_dart_report, make_dart_event_hash
from app.orchestrator.persistence import CollectionPersistence
from app.orchestrator.task_types import NORMALIZE_DART
from signal_alpha_data_access.repositories import DartRepository, NormalizationRepository, RawDetailRepository


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


class DartNormalizeTaskHandler:
    def __init__(self, connection: Any) -> None:
        self._raw_detail_repository = RawDetailRepository(connection)
        self._normalization_repository = NormalizationRepository(connection)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        task_context = _task_context(task.get("task_context"))
        stock_code = _stock_code_from_context(task_context)
        raw_document_ids = _source_raw_ids(task.get("source_raw_ids"))
        rows = await self._raw_detail_repository.list_dart_documents_by_raw_ids(raw_document_ids)

        signal_event_ids: list[int] = []
        for row in rows:
            classification = classify_dart_report(row["report_name"])
            source_document = await self._normalization_repository.upsert_source_document(
                raw_document_id=row["raw_document_id"],
                stock_id=row["stock_id"],
                source_type="DART",
                source_name=row["source_name"],
                title=row["title"],
                source_url=row["source_url"],
                published_at=row["published_at"],
                collected_at=row["collected_at"],
                reliability_level="high",
                is_official=True,
            )
            signal_event = await self._normalization_repository.upsert_signal_event(
                stock_id=row["stock_id"],
                source_document_id=source_document["id"],
                event_hash=make_dart_event_hash(stock_code, row["receipt_no"], row["report_name"]),
                source_type="DART",
                event_type=classification.event_type,
                event_date=_to_date(row["published_at"]),
                signal_direction=classification.signal_direction,
                impact_level=classification.impact_level,
                title=row["report_name"],
                summary=f"DART disclosure: {row['report_name']}",
                evidence_text=row["report_name"],
                evidence_url=row["source_url"],
                needs_review=classification.needs_review,
            )
            signal_event_ids.append(signal_event["id"])
            await self._normalization_repository.upsert_signal_metric(
                signal_event_id=signal_event["id"],
                metric_name="dart_disclosure_count",
                metric_value=1,
                metric_unit="count",
            )
            await self._normalization_repository.record_validation_log(
                target_type="signal_event",
                target_id_int=signal_event["id"],
                validation_type="source_trace",
                passed=True,
                message=f"Normalized from raw_document_id={row['raw_document_id']}",
            )

        return {
            "normalized_count": len(rows),
            "signal_event_ids": signal_event_ids,
        }


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


def _source_raw_ids(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        return [int(item) for item in value]
    if isinstance(value, tuple):
        return [int(item) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") and text.endswith("}"):
            inner = text[1:-1].strip()
            if not inner:
                return []
            return [int(item.strip()) for item in inner.split(",")]
        parsed = json.loads(text)
        return [int(item) for item in parsed]
    return [int(value)]


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return datetime.fromisoformat(text[:10]).date()
