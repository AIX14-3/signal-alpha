from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from app.analyzers.dart.source_result import build_dart_analysis_result
from app.collectors.dart.disclosure import DartCollector, DartDisclosureClient
from app.analyzers.dart.rules import classify_dart_report, make_dart_event_hash
from app.orchestrator.persistence import CollectionPersistence
from app.orchestrator.queue.task_types import ANALYZE_DART, NORMALIZE_DART
from signal_alpha_data_access.repositories import (
    AnalysisRepository,
    DartRepository,
    NormalizationRepository,
    ProcessingQueueRepository,
    RawDetailRepository,
)


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
        dart_repository = DartRepository(self._connection)
        date_window = await _resolve_collection_window(
            repository=dart_repository,
            stock_code=stock_code,
            task_context=task_context,
        )
        if date_window.get("skip_reason"):
            return {
                "collector_run_id": None,
                "collected_count": 0,
                "inserted_count": 0,
                "raw_document_ids": [],
                "skipped_reason": date_window["skip_reason"],
                "last_end_de": date_window.get("last_end_de"),
                "end_de": date_window["end_de"],
            }

        collector = DartCollector(
            api_key=self._settings.dart_api_key,
            corp_code_repository=dart_repository,
            client=self._client or _dart_client_from_settings(self._settings),
            start_date=date_window["bgn_de"],
            end_date=date_window["end_de"],
            page_size=self._settings.dart_page_size,
            fetch_documents=getattr(self._settings, "dart_fetch_documents", True),
        )
        evidence = await collector.collect(stock_code)
        result = await CollectionPersistence(self._connection).save_evidence_batch(
            stock_id=stock_id,
            stock_code=stock_code,
            evidence=evidence,
            collector_type="DART",
            enqueue_task_type=NORMALIZE_DART,
        )
        await dart_repository.upsert_collection_state(
            stock_id=stock_id,
            ticker=stock_code,
            last_bgn_de=date_window["bgn_de"],
            last_end_de=date_window["end_de"],
            last_receipt_no=_last_receipt_no(evidence),
            last_collected_count=result["collected_count"],
            last_collector_run_id=result["collector_run_id"],
        )
        return result


class DartNormalizeTaskHandler:
    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._raw_detail_repository = RawDetailRepository(connection)
        self._normalization_repository = NormalizationRepository(connection)
        self._queue_repository = ProcessingQueueRepository(connection)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        task_context = _task_context(task.get("task_context"))
        stock_code = _stock_code_from_context(task_context)
        raw_document_ids = _source_raw_ids(task.get("source_raw_ids"))
        rows = await self._raw_detail_repository.list_dart_documents_by_raw_ids(raw_document_ids)

        signal_event_ids: list[int] = []
        for row in rows:
            classification = classify_dart_report(
                row["report_name"],
                is_correction=_truthy(row.get("is_correction")),
            )
            evidence_text = _dart_evidence_text(row)
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
                evidence_text=evidence_text,
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

        analysis_task_id: int | None = None
        if signal_event_ids:
            analysis_task_id = await self._queue_repository.enqueue(
                stock_id=int(rows[0]["stock_id"]),
                task_type=ANALYZE_DART,
                priority="batch",
                source_signal_event_ids=signal_event_ids,
                task_context={"stock_code": stock_code, "source_type": "DART"},
                dedupe=True,
            )

        return {
            "normalized_count": len(rows),
            "signal_event_ids": signal_event_ids,
            "analysis_task_id": analysis_task_id,
        }


class DartAnalyzeTaskHandler:
    def __init__(self, connection: Any) -> None:
        self._normalization_repository = NormalizationRepository(connection)
        self._analysis_repository = AnalysisRepository(connection)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        signal_event_ids = _source_signal_event_ids(task.get("source_signal_event_ids"))
        rows = await self._normalization_repository.list_signal_events_by_ids(signal_event_ids)
        events = [dict(row) for row in rows]
        result = build_dart_analysis_result(events)
        analysis_date = _analysis_date(events)
        run_key = _run_key(task_context)

        analysis_result = await self._analysis_repository.upsert_analysis_result(
            stock_id=stock_id,
            analysis_date=analysis_date,
            run_key=run_key,
            source_signal_event_ids=signal_event_ids,
            base_score=result.score,
            analysis_mode="dart_only",
            warning="; ".join(result.risk_flags) or None,
            version="dart-rules-v1",
        )
        agent_result = await self._analysis_repository.upsert_agent_result(
            result_id=analysis_result["id"],
            stock_id=stock_id,
            debate_method="D-1",
            source_signal_event_ids=signal_event_ids,
            method_score=result.score,
            method_signal=result.direction,
            method_detail={
                **result.method_detail,
                "summary": result.summary,
                "risk_flags": result.risk_flags,
                "needs_review": result.needs_review,
                "stock_code": task_context.get("stock_code"),
            },
            reliability_score=90,
            evidence_quality=_evidence_quality(events),
            prompt_ver="dart-rules-v1",
        )
        return {
            "analysis_result_id": analysis_result["id"],
            "agent_result_id": agent_result["id"],
            "analyzed_count": len(events),
            "direction": result.direction,
            "score": result.score,
            "needs_review": result.needs_review,
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


def _source_signal_event_ids(value: Any) -> list[int]:
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


async def _resolve_collection_window(
    *,
    repository: DartRepository,
    stock_code: str,
    task_context: dict[str, Any],
) -> dict[str, str]:
    end_de = _yyyymmdd(task_context.get("end_de")) or _today_yyyymmdd()
    bgn_de = _yyyymmdd(task_context.get("bgn_de"))
    if bgn_de:
        return {"bgn_de": bgn_de, "end_de": end_de}

    state = await repository.get_collection_state_by_ticker(stock_code)
    if state and state.get("last_end_de"):
        next_bgn_de = _next_yyyymmdd(state["last_end_de"])
        if next_bgn_de > end_de:
            return {
                "bgn_de": next_bgn_de,
                "end_de": end_de,
                "last_end_de": _yyyymmdd(state["last_end_de"]),
                "skip_reason": "dart_collection_up_to_date",
            }
        return {
            "bgn_de": next_bgn_de,
            "end_de": end_de,
        }
    return {"bgn_de": _default_bgn_de(end_de), "end_de": end_de}


def _last_receipt_no(evidence: list[Any]) -> str | None:
    if not evidence:
        return None
    return evidence[-1].metadata.get("receipt_no")


def _yyyymmdd(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return text
    return datetime.fromisoformat(text[:10]).strftime("%Y%m%d")


def _next_yyyymmdd(value: Any) -> str:
    parsed = datetime.strptime(_yyyymmdd(value), "%Y%m%d").date()
    return (parsed + timedelta(days=1)).strftime("%Y%m%d")


def _default_bgn_de(end_de: str) -> str:
    parsed = datetime.strptime(end_de, "%Y%m%d").date()
    return (parsed - timedelta(days=30)).strftime("%Y%m%d")


def _today_yyyymmdd() -> str:
    return date.today().strftime("%Y%m%d")


def _dart_client_from_settings(settings: Any) -> DartDisclosureClient:
    return DartDisclosureClient(
        api_key=settings.dart_api_key,
        base_url=getattr(settings, "dart_base_url", "https://opendart.fss.or.kr/api"),
        timeout_seconds=getattr(settings, "dart_timeout_seconds", 10),
        max_retries=getattr(settings, "dart_max_retries", 2),
        retry_backoff_seconds=getattr(settings, "dart_retry_backoff_seconds", 0.5),
    )


def _dart_evidence_text(row: Mapping[str, Any]) -> str:
    extra_payload = row.get("extra_payload") or {}
    if isinstance(extra_payload, str):
        extra_payload = json.loads(extra_payload)
    document_text = extra_payload.get("document_text")
    if document_text:
        return str(document_text)
    return str(row["report_name"])


def _truthy(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes", "y"}


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return datetime.fromisoformat(text[:10]).date()


def _analysis_date(events: list[dict[str, Any]]) -> date:
    if not events:
        return date.today()
    event_dates = [_to_date(event["event_date"]) for event in events if event.get("event_date")]
    if not event_dates:
        return date.today()
    return max(event_dates)


def _run_key(task_context: dict[str, Any]) -> str:
    return str(task_context.get("run_key") or "DART").strip() or "DART"


def _evidence_quality(events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    official_count = sum(1 for event in events if event.get("is_official"))
    return round((official_count / len(events)) * 100)
