from __future__ import annotations

from hashlib import sha256
from datetime import date, datetime, time
from typing import Any

from app.schemas.evidence import RawEvidence, SourceType
from signal_alpha_data_access.repositories import (
    CollectionRepository,
    ProcessingQueueRepository,
    RawDetailRepository,
)


class CollectionPersistence:
    def __init__(self, connection: Any) -> None:
        self._connection = connection
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
        queued_task_ids: list[int] = []
        failed_count = 0
        last_error: Exception | None = None
        for item in evidence:
            try:
                # CLAUDE.md Transaction Rule: raw_documents + detail + processing_queue 를
                # record 당 한 트랜잭션으로 묶는다. enqueue/detail 가 실패하면 raw/detail 까지
                # 롤백되어 "큐 엔트리 없는 고아 raw 행"이 남지 않는다(재수집 시 upsert skip 으로
                # 영구 미정규화되는 문제 방지). collector_runs 는 트랜잭션 밖에서 기록.
                async with self._connection.transaction():
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
                    is_inserted = _raw_document_inserted(raw_document)
                    await self._save_source_detail(raw_document_id, stock_id, item)

                    task_id: int | None = None
                    if enqueue_task_type and (is_inserted or force_reprocess):
                        task_id = await self._queue_repository.enqueue(
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
            except Exception as exc:
                # 이 record 의 raw/detail/enqueue 는 트랜잭션과 함께 롤백됨.
                failed_count += 1
                last_error = exc
                continue

            raw_document_ids.append(raw_document_id)
            if is_inserted:
                inserted_raw_ids.append(raw_document_id)
            elif force_reprocess:
                reprocessed_raw_ids.append(raw_document_id)
            if task_id is not None:
                queued_task_ids.append(task_id)

        status = _run_status(usable=len(raw_document_ids), failed=failed_count)
        skipped_count = len(raw_document_ids) - len(inserted_raw_ids) - len(reprocessed_raw_ids)
        await self._collection_repository.finish_collector_run(
            run_id=run_id,
            status=status,
            collected_count=len(evidence),
            inserted_count=len(inserted_raw_ids),
            skipped_count=skipped_count,
            failed_count=failed_count,
            error_message=str(last_error) if last_error is not None else None,
        )
        # 전건 실패(처리된 record 0 + 실패 존재) → 큐 재시도/DLQ 로 넘기기 위해 raise.
        # 부분 실패(partial)는 정상 반환 — 성공 record 는 이미 커밋되었고 run 에 partial 로 기록됨.
        if not raw_document_ids and failed_count:
            assert last_error is not None
            raise last_error

        return {
            "collector_run_id": run_id,
            "collected_count": len(evidence),
            "inserted_count": len(inserted_raw_ids),
            "skipped_count": skipped_count,
            "reprocessed_count": len(reprocessed_raw_ids),
            "failed_count": failed_count,
            "status": status,
            "raw_document_ids": raw_document_ids,
            "new_raw_document_ids": inserted_raw_ids,
            "reprocessed_raw_document_ids": reprocessed_raw_ids,
            "queued_task_ids": queued_task_ids,
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


def _run_status(*, usable: int, failed: int) -> str:
    """CLAUDE.md Error Handling 규칙의 카운트 기반 collector_run status.

    usable = 실패 없이 처리된 record 수(inserted + skipped + reprocessed).
    """
    if failed == 0:
        return "success"
    if usable > 0:
        return "partial"
    return "failed"


def _infer_collector_type(evidence: list[RawEvidence]) -> SourceType:
    if not evidence:
        return "REPORT"
    return evidence[0].source


def _source_hash(item: RawEvidence) -> str:
    # patent/datalab 의 소스별 키(PATENT|application_no 등)와 달리, DART/REPORT 경로는
    # 별도 키가 없어 content 기반 제네릭 핑거프린트를 쓴다. dedup 키는 (source_type,
    # external_id)이며 이 hash 는 변경 감지용. CLAUDE.md 정규화 규칙(trim·lower·NULL→empty·
    # "|" join)을 따른다.
    parts = [
        item.source,
        item.stock_code,
        item.title,
        item.published_at or "",
        item.url or "",
        item.content,
    ]
    stable_text = "|".join((part or "").strip().lower() for part in parts)
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
