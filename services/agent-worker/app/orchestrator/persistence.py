from __future__ import annotations

from hashlib import sha256
from datetime import date, datetime, time
from typing import Any

from app.schemas.evidence import RawEvidence, SourceType
from app.schemas.source_result import Direction, EvidenceItem, SourceResult
from signal_alpha_data_access.repositories import (
    AnalysisRepository,
    CollectionRepository,
    NormalizationRepository,
    ProcessingQueueRepository,
    RawDetailRepository,
)


class CollectionPersistence:
    def __init__(self, connection: Any) -> None:
        self._collection_repository = CollectionRepository(connection)
        self._queue_repository = ProcessingQueueRepository(connection)
        self._raw_detail_repository = RawDetailRepository(connection)

    async def save_evidence_batch(
        self,
        *,
        stock_id: int,
        stock_code: str,
        evidence: list[RawEvidence],
        collector_type: SourceType | None = None,
        run_mode: str = "batch",
        enqueue_task_type: str | None = None,
        force_reprocess: bool = False,
    ) -> dict[str, Any]:
        resolved_collector_type = collector_type or _infer_collector_type(evidence)
        run_id = await self._collection_repository.create_collector_run(
            resolved_collector_type,
            run_mode,
        )

        raw_document_ids: list[int] = []
        inserted_raw_ids: list[int] = []
        reprocessed_raw_ids: list[int] = []
        try:
            for item in evidence:
                raw_document = await self._collection_repository.upsert_raw_document(
                    stock_id=stock_id,
                    collector_run_id=run_id,
                    source_type=item.source,
                    source_name=item.metadata.get("source_name", item.source),
                    external_id=_external_id(item),
                    source_hash=_source_hash(item),
                    title=item.title,
                    source_url=item.url,
                    published_at=_to_datetime(item.published_at or item.metadata.get("published_at")),
                    collector_ver=item.metadata.get("collector_ver", "1.0"),
                )
                raw_document_id = raw_document["id"]
                raw_document_ids.append(raw_document_id)
                is_inserted = _raw_document_inserted(raw_document)
                if is_inserted:
                    inserted_raw_ids.append(raw_document_id)
                elif force_reprocess:
                    reprocessed_raw_ids.append(raw_document_id)
                await self._save_source_detail(raw_document_id, stock_id, item)

                if enqueue_task_type and (is_inserted or force_reprocess):
                    await self._queue_repository.enqueue(
                        stock_id=stock_id,
                        task_type=enqueue_task_type,
                        priority=item.metadata.get("priority", "batch"),
                        source_raw_ids=[raw_document_id],
                        task_context={
                            "stock_code": stock_code,
                            "source_type": item.source,
                        },
                        dedupe=True,
                    )

            await self._collection_repository.finish_collector_run(
                run_id=run_id,
                status="success",
                collected_count=len(evidence),
                inserted_count=len(inserted_raw_ids),
            )
        except Exception as exc:
            await self._collection_repository.finish_collector_run(
                run_id=run_id,
                status="failed",
                collected_count=len(evidence),
                inserted_count=len(inserted_raw_ids),
                failed_count=max(1, len(evidence) - len(raw_document_ids)),
                error_message=str(exc),
            )
            raise

        return {
            "collector_run_id": run_id,
            "collected_count": len(evidence),
            "inserted_count": len(inserted_raw_ids),
            "skipped_count": len(raw_document_ids) - len(inserted_raw_ids) - len(reprocessed_raw_ids),
            "reprocessed_count": len(reprocessed_raw_ids),
            "raw_document_ids": raw_document_ids,
            "new_raw_document_ids": inserted_raw_ids,
            "reprocessed_raw_document_ids": reprocessed_raw_ids,
        }

    async def _save_source_detail(
        self,
        raw_document_id: int,
        stock_id: int,
        item: RawEvidence,
    ) -> None:
        if item.source == "REPORT":
            await self._collection_repository.upsert_report_detail(
                raw_document_id=raw_document_id,
                stock_id=stock_id,
                securities_firm=item.metadata.get("securities_firm", item.metadata.get("source_name", "unknown")),
                publish_date=_to_date(item.metadata.get("publish_date", item.published_at)),
                analyst_name=item.metadata.get("analyst_name"),
                investment_opinion=item.metadata.get("investment_opinion"),
                target_price=_optional_int(item.metadata.get("target_price")),
                previous_target_price=_optional_int(item.metadata.get("previous_target_price")),
                current_price_at_publish=_optional_int(item.metadata.get("current_price_at_publish")),
                upside_pct=item.metadata.get("upside_pct"),
                has_pdf=bool(item.metadata.get("pdf_url") or item.url),
                pdf_url=item.metadata.get("pdf_url", item.url),
                extracted_text=item.content,
                parsing_status=item.metadata.get("parsing_status", "success"),
                extra_payload=dict(item.metadata),
            )
            await self._collection_repository.replace_report_chunks(
                raw_document_id=raw_document_id,
                stock_id=stock_id,
                chunks=[item.content],
            )
        elif item.source == "DART":
            await self._raw_detail_repository.upsert_dart_detail(
                raw_document_id=raw_document_id,
                stock_id=stock_id,
                receipt_no=item.metadata.get("receipt_no", _external_id(item)),
                report_name=item.metadata.get("report_name", item.title),
                corp_code=item.metadata.get("corp_code"),
                disclosure_type=item.metadata.get("disclosure_type"),
                priority=item.metadata.get("priority", "batch"),
                priority_reason=item.metadata.get("priority_reason"),
                is_correction=_truthy(item.metadata.get("is_correction")),
                original_receipt_no=item.metadata.get("original_receipt_no"),
                extra_payload=dict(item.metadata),
            )
        elif item.source == "HIRING":
            await self._raw_detail_repository.upsert_hiring_detail(
                raw_document_id=raw_document_id,
                stock_id=stock_id,
                keyword=item.metadata.get("keyword"),
                job_category=item.metadata.get("job_category"),
                job_count=_optional_int(item.metadata.get("job_count")),
                previous_job_count=_optional_int(item.metadata.get("previous_job_count")),
                change_pct=item.metadata.get("change_pct"),
                extra_payload=dict(item.metadata),
            )
        elif item.source == "PATENT":
            await self._raw_detail_repository.upsert_patent_detail(
                raw_document_id=raw_document_id,
                stock_id=stock_id,
                application_no=item.metadata.get("application_no", _external_id(item)),
                patent_title=item.metadata.get("patent_title", item.title),
                application_date=_to_date(item.metadata.get("application_date", item.published_at)),
                applicant_name=item.metadata.get("applicant_name"),
                tech_category=item.metadata.get("tech_category"),
                is_new_category=_truthy(item.metadata.get("is_new_category")),
                extra_payload=dict(item.metadata),
            )
        elif item.source == "DATALAB":
            await self._raw_detail_repository.upsert_datalab_detail(
                raw_document_id=raw_document_id,
                stock_id=stock_id,
                keyword=item.metadata.get("keyword", item.title),
                observed_date=_to_date(item.metadata.get("observed_date", item.published_at)),
                search_index=item.metadata.get("search_index", 0),
                keyword_group=item.metadata.get("keyword_group"),
                previous_search_index=item.metadata.get("previous_search_index"),
                change_pct=item.metadata.get("change_pct"),
                period_type=item.metadata.get("period_type", "daily"),
                device=item.metadata.get("device", "all"),
                gender=item.metadata.get("gender", "all"),
                age_group=item.metadata.get("age_group", "all"),
                is_spike=_truthy(item.metadata.get("is_spike")),
                extra_payload=dict(item.metadata),
            )


class AnalysisPersistence:
    def __init__(self, connection: Any) -> None:
        self._analysis_repository = AnalysisRepository(connection)
        self._normalization_repository = NormalizationRepository(connection)

    async def save_source_result(
        self,
        *,
        stock_id: int,
        stock_code: str,
        source_result: SourceResult,
        source_raw_ids: list[int],
        run_key: str = "BATCH",
        version: str = "1.0",
        publish_final_signal: bool = False,
    ) -> dict[str, Any]:
        signal_event_ids: list[int] = []
        for index, evidence_item in enumerate(source_result.evidence_items):
            raw_document_id = _raw_id_for_evidence(source_raw_ids, index)
            source_document = await self._normalization_repository.upsert_source_document(
                raw_document_id=raw_document_id,
                stock_id=stock_id,
                source_type=source_result.source,
                source_name=evidence_item.source_name or source_result.source,
                title=evidence_item.title,
                source_url=evidence_item.url,
                published_at=evidence_item.published_at or _today(),
                collected_at=_today(),
                reliability_level="medium",
                is_official=source_result.source == "DART",
            )
            signal_event = await self._normalization_repository.upsert_signal_event(
                stock_id=stock_id,
                source_document_id=source_document["id"],
                event_hash=_event_hash(stock_code, source_result, evidence_item),
                source_type=source_result.source,
                event_type=f"{source_result.source.lower()}_analysis",
                event_date=_date_part(evidence_item.published_at),
                signal_direction=source_result.direction,
                impact_level="medium",
                title=evidence_item.title,
                summary=evidence_item.summary,
                evidence_text=evidence_item.summary,
                evidence_url=evidence_item.url,
                needs_review=source_result.data_status != "ok",
            )
            signal_event_ids.append(signal_event["id"])
            await self._normalization_repository.upsert_signal_metric(
                signal_event_id=signal_event["id"],
                metric_name="source_score",
                metric_value=source_result.score,
                metric_unit="score",
            )

        analysis_result = await self._analysis_repository.upsert_analysis_result(
            stock_id=stock_id,
            analysis_date=_today(),
            run_key=run_key,
            source_signal_event_ids=signal_event_ids,
            base_score=source_result.score,
            analysis_mode="full",
            warning="; ".join(source_result.risk_flags) or None,
            version=version,
        )
        agent_result = await self._analysis_repository.upsert_agent_result(
            result_id=analysis_result["id"],
            stock_id=stock_id,
            debate_method="D-1",
            source_signal_event_ids=signal_event_ids,
            method_score=source_result.score,
            method_signal=_final_signal_value(source_result.direction),
            method_detail={
                "source": source_result.source,
                "summary": source_result.summary,
                "risk_flags": source_result.risk_flags,
                "data_status": source_result.data_status,
            },
        )

        final_signal_id: int | None = None
        if publish_final_signal:
            final_signal = await self._analysis_repository.upsert_final_signal(
                stock_id=stock_id,
                analysis_result_id=analysis_result["id"],
                signal_date=_today(),
                run_key=run_key,
                version=version,
                final_score=source_result.score,
                confidence=source_result.score,
                signal=_final_signal_value(source_result.direction),
                source_agreement="MEDIUM",
                score_breakdown={source_result.source: source_result.score},
                summary=source_result.summary,
                warning_level="NORMAL" if source_result.data_status == "ok" else "CAUTION",
                needs_review=source_result.data_status != "ok",
                is_published=True,
                published_at=_today(),
            )
            final_signal_id = final_signal["id"]

        return {
            "analysis_result_id": analysis_result["id"],
            "agent_result_id": agent_result["id"],
            "final_signal_id": final_signal_id,
            "signal_event_ids": signal_event_ids,
        }


def _infer_collector_type(evidence: list[RawEvidence]) -> SourceType:
    if not evidence:
        return "REPORT"
    return evidence[0].source


def _source_hash(item: RawEvidence) -> str:
    stable_text = "|".join(
        [
            item.source,
            item.stock_code,
            item.title,
            item.published_at or "",
            item.url or "",
            item.content,
        ]
    )
    return sha256(stable_text.encode("utf-8")).hexdigest()


def _raw_document_inserted(raw_document: Any) -> bool:
    try:
        return bool(raw_document["inserted"])
    except (KeyError, IndexError, TypeError):
        return True


def _external_id(item: RawEvidence) -> str:
    return item.metadata.get("external_id") or item.url or _source_hash(item)


def _optional_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _truthy(value: str | None) -> bool:
    return str(value).lower() in {"1", "true", "yes", "y"}


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if not value:
        return datetime.combine(date.today(), time.min)

    text = str(value)
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d")
    return datetime.fromisoformat(text)


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return date.today()

    text = str(value)
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return datetime.fromisoformat(text).date()


def _raw_id_for_evidence(source_raw_ids: list[int], index: int) -> int:
    if not source_raw_ids:
        raise ValueError("source_raw_ids is required to persist normalized evidence.")
    if index < len(source_raw_ids):
        return source_raw_ids[index]
    return source_raw_ids[0]


def _event_hash(
    stock_code: str,
    source_result: SourceResult,
    evidence_item: EvidenceItem,
) -> str:
    stable_text = "|".join(
        [
            stock_code,
            source_result.source,
            evidence_item.title,
            evidence_item.published_at or "",
            evidence_item.url or "",
        ]
    )
    return sha256(stable_text.encode("utf-8")).hexdigest()


def _date_part(value: str | None) -> str:
    if not value:
        return _today()
    return value[:10]


def _today() -> str:
    return date.today().isoformat()


def _final_signal_value(direction: Direction) -> str:
    if direction == "unknown":
        return "neutral"
    return direction
