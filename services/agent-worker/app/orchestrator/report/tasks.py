from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from app.collectors.report.crawler import collect_stock
from app.collectors.report.parsers.run_parser import process_from_s3
from app.collectors.report.pdf_downloader import download_and_upload, make_report_storage_key
from app.collectors.report.storage import ReportStorageClient, get_report_storage_client
from app.orchestrator.queue.task_types import (
    ANALYZE_REPORT,
    NORMALIZE_REPORT,
    PROCESS_REPORT,
)
from signal_alpha_data_access.repositories import (
    AnalysisRepository,
    CollectionRepository,
    NormalizationRepository,
    ProcessingQueueRepository,
    RawDetailRepository,
)

logger = logging.getLogger(__name__)


class ReportCollectTaskHandler:
    """
    1. stock_code 기준으로 크롤러 실행
    2. raw_documents + report_raw_details에 저장 (source_hash 중복 방지, 트랜잭션)
    3. 각 리포트마다 process_report 태스크 등록
    """

    def __init__(self, *, connection: Any, settings: Any) -> None:
        self._connection = connection
        self._settings = settings

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        stock_code = task_context.get("stock_code", "")
        days_back = int(task_context.get("days_back", 7))
        max_pages = int(task_context.get("max_pages", 20))
        collection_repository = CollectionRepository(self._connection)
        collector_run_id = await collection_repository.create_collector_run("REPORT", "batch")

        reports: list[dict] = []
        save_result = _new_report_collection_result()
        enqueued_count = 0
        try:
            # 절대 날짜(date_start/date_end)가 있으면 우선, 없으면 days_back으로 fallback.
            ds = task_context.get("date_start")
            de = task_context.get("date_end")
            date_end = datetime.fromisoformat(de) if de else datetime.now()
            date_start = datetime.fromisoformat(ds) if ds else date_end - timedelta(days=days_back)

            reports = collect_stock(
                stock_name="",
                stock_code=stock_code,
                max_pages=max_pages,
                date_start=date_start,
                date_end=date_end,
            )

            save_result = await _save_to_db(
                collection_repository,
                reports,
                stock_id,
                collector_run_id=collector_run_id,
            )

            queue = ProcessingQueueRepository(self._connection)
            for raw_document_id in save_result["raw_document_ids"]:
                await queue.enqueue(
                    stock_id=stock_id,
                    task_type=PROCESS_REPORT,
                    priority="batch",
                    source_raw_ids=[raw_document_id],
                    task_context={
                        "stock_code": stock_code,
                        "raw_document_id": raw_document_id,
                    },
                    dedupe=True,
                )
                enqueued_count += 1
        except Exception as exc:
            await collection_repository.finish_collector_run(
                run_id=collector_run_id,
                status="failed",
                collected_count=len(reports),
                inserted_count=save_result["inserted_reports"],
                skipped_count=save_result["duplicate_reports"] + save_result["invalid_date_reports"],
                failed_count=1,
                error_message=str(exc),
            )
            logger.warning(
                "report_collection_summary %s",
                json.dumps(
                    _report_collection_log_payload(
                        status="failed",
                        collector_run_id=collector_run_id,
                        stock_id=stock_id,
                        stock_code=stock_code,
                        reports=reports,
                        save_result=save_result,
                        enqueued_count=enqueued_count,
                        error_message=str(exc),
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
            raise

        await collection_repository.finish_collector_run(
            run_id=collector_run_id,
            status="success",
            collected_count=len(reports),
            inserted_count=save_result["inserted_reports"],
            skipped_count=save_result["duplicate_reports"] + save_result["invalid_date_reports"],
            failed_count=0,
        )

        logger.info(
            "report_collection_summary %s",
            json.dumps(
                _report_collection_log_payload(
                    status="success",
                    collector_run_id=collector_run_id,
                    stock_id=stock_id,
                    stock_code=stock_code,
                    reports=reports,
                    save_result=save_result,
                    enqueued_count=enqueued_count,
                ),
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

        return {
            "collector_run_id": collector_run_id,
            "collected": save_result["saved_reports"],
            "collected_reports": len(reports),
            "saved_reports": save_result["saved_reports"],
            "inserted_reports": save_result["inserted_reports"],
            "duplicate_reports": save_result["duplicate_reports"],
            "invalid_date_reports": save_result["invalid_date_reports"],
            "missing_pdf_reports": save_result["missing_pdf_reports"],
            "enqueued_reports": enqueued_count,
            "skip_reasons": save_result["skip_reasons"],
        }


class ReportProcessTaskHandler:
    """
    1. report_raw_details에서 pdf_url, s3_key 확인
    2. PDF 다운로드 → report storage 업로드 (s3_key 미존재 시)
    3. 전체 텍스트 기반 파싱 (규칙 기반 fallback, REPORT_USE_LLM=true일 때만 LLM 보강)
    4. report_raw_details 업데이트 (parsing_status='success', target_price 등)
    """

    def __init__(
        self,
        *,
        connection: Any,
        settings: Any,
        storage: ReportStorageClient | None = None,
    ) -> None:
        self._connection = connection
        self._settings = settings
        self._storage = storage
        self._s3 = storage

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        task_context = _task_context(task.get("task_context"))
        raw_document_id = int(task_context["raw_document_id"])

        row = await self._connection.fetchrow(
            """
            SELECT rd.stock_id,
                   rd.source_url     AS pdf_url,
                   rd.source_hash,
                   s.ticker          AS stock_code,
                   rrd.securities_firm,
                   rrd.publish_date,
                   rrd.extra_payload,
                   rrd.s3_key,
                   rrd.parsing_status
            FROM raw_documents rd
            JOIN report_raw_details rrd ON rrd.raw_document_id = rd.id
            JOIN stocks s               ON s.id = rd.stock_id
            WHERE rd.id = $1
            """,
            raw_document_id,
        )

        if row is None:
            return {"status": "not_found", "raw_document_id": raw_document_id}

        if row["parsing_status"] == "success":
            return {"status": "already_done", "raw_document_id": raw_document_id}

        extra = _extra_payload(row["extra_payload"])
        s3_key = row["s3_key"] or make_report_storage_key(str(row["stock_code"]), {
            "firm": row["securities_firm"],
            "date": str(row["publish_date"]),
            "report_type": extra.get("report_type") or "cr",
            "source_hash": row["source_hash"],
        })

        if not row["pdf_url"]:
            await self._mark_failed(raw_document_id, "pdf_url이 없습니다")
            return {"status": "no_pdf_url"}

        storage = self._get_storage()

        if not storage.exists(s3_key):
            success = download_and_upload(row["pdf_url"], s3_key, storage)
            if not success:
                await self._mark_failed(raw_document_id, "PDF 다운로드 실패")
                return {"status": "download_failed"}

        parsed = process_from_s3(s3_key, storage, settings=self._settings)

        await self._connection.execute(
            """
            UPDATE report_raw_details
            SET s3_key             = $2,
                has_pdf            = TRUE,
                parsing_status     = 'success',
                parsed_at          = NOW(),
                investment_opinion = $3,
                target_price       = $4,
                key_rationale      = $5,
                extracted_text     = $6
            WHERE raw_document_id = $1
            """,
            raw_document_id,
            s3_key,
            parsed.get("opinion"),
            parsed.get("target_price"),
            parsed.get("key_rationale"),
            parsed.get("raw_text", "")[:2000],
        )

        # 밸류에이션 fact 추출(PROCESS 단계, 임베딩 무관) → report_valuation_facts 적재.
        valuation_facts = dict(parsed.get("valuation_facts") or {})
        await CollectionRepository(self._connection).upsert_report_valuation_fact(
            raw_document_id=raw_document_id,
            stock_id=int(row["stock_id"]),
            ticker=str(row["stock_code"]),
            broker=row["securities_firm"],
            publish_date=row["publish_date"],
            target_price=valuation_facts.get("target_price") or parsed.get("target_price"),
            forward_eps_est=valuation_facts.get("forward_eps_est"),
            eps_fy=valuation_facts.get("eps_fy"),
            methodology=valuation_facts.get("methodology") or "unknown",
            applied_multiple=valuation_facts.get("applied_multiple"),
            implied_multiple=valuation_facts.get("implied_multiple"),
            peer_group=valuation_facts.get("peer_group") or [],
            category_tag=valuation_facts.get("category_tag"),
            rerating_thesis=valuation_facts.get("rerating_thesis"),
            extraction_source=valuation_facts.get("extraction_source") or "rules",
            needs_review=bool(valuation_facts.get("needs_review", True)),
        )

        # 파싱 완료 → 정규화 태스크 자동 등록. 파싱 실패와 격리하고 독립 재시도하도록
        # 별도 normalize_report 태스크로 분리한다.
        queue = ProcessingQueueRepository(self._connection)
        normalize_task_id = await queue.enqueue(
            stock_id=int(row["stock_id"]),
            task_type=NORMALIZE_REPORT,
            priority="batch",
            source_raw_ids=[raw_document_id],
            task_context={
                "raw_document_id": raw_document_id,
                "stock_code": str(row["stock_code"]),
                "source_type": "REPORT",
            },
            dedupe=True,
        )

        return {
            "status": "success",
            "raw_document_id": raw_document_id,
            "s3_key": s3_key,
            "normalize_task_id": normalize_task_id,
        }

    def _get_storage(self) -> ReportStorageClient:
        if self._storage is None:
            self._storage = get_report_storage_client(self._settings)
            self._s3 = self._storage
        return self._storage

    async def _mark_failed(self, raw_document_id: int, error: str) -> None:
        await self._connection.execute(
            """
            UPDATE report_raw_details
            SET parsing_status = 'failed',
                parsing_error  = $2
            WHERE raw_document_id = $1
            """,
            raw_document_id,
            error,
        )


class ReportNormalizeTaskHandler:
    """Promote parsed Report raw rows to the canonical source/event/metric path."""

    SOURCE_TYPE = "REPORT"
    RELIABILITY_LEVEL = "medium"
    IS_OFFICIAL = False

    def __init__(self, *, connection: Any) -> None:
        self._raw_detail_repository = RawDetailRepository(connection)
        self._normalization_repository = NormalizationRepository(connection)
        self._queue_repository = ProcessingQueueRepository(connection)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        task_context = _task_context(task.get("task_context"))
        raw_document_ids = _source_raw_ids(task.get("source_raw_ids"))
        if not raw_document_ids and task_context.get("raw_document_id") is not None:
            raw_document_ids = [int(task_context["raw_document_id"])]

        rows = await self._raw_detail_repository.list_report_details_by_raw_ids(raw_document_ids)

        signal_event_ids: list[int] = []
        for row in rows:
            raw_document_id = int(row["raw_document_id"])
            stock_id = int(row["stock_id"])
            source_document = await self._normalization_repository.upsert_source_document(
                raw_document_id=raw_document_id,
                stock_id=stock_id,
                source_type=self.SOURCE_TYPE,
                source_name=str(row.get("securities_firm") or row.get("source_name") or "REPORT"),
                title=str(row.get("title") or "증권사 리포트"),
                source_url=row.get("source_url"),
                published_at=row["published_at"],
                collected_at=row["collected_at"],
                reliability_level=self.RELIABILITY_LEVEL,
                is_official=self.IS_OFFICIAL,
            )
            signal_event = await self._normalization_repository.upsert_signal_event(
                stock_id=stock_id,
                source_document_id=int(source_document["id"]),
                event_hash=_report_event_hash(raw_document_id),
                source_type=self.SOURCE_TYPE,
                event_type="report_published",
                event_date=_to_date(row.get("publish_date") or row["published_at"]),
                signal_direction=_report_signal_direction(row.get("investment_opinion")),
                impact_level=_report_impact_level(row),
                title=str(row.get("title") or "증권사 리포트"),
                summary=_report_summary(row),
                evidence_text=_report_evidence_text(row),
                evidence_url=row.get("source_url"),
                needs_review=_report_needs_review(row),
            )
            signal_event_id = int(signal_event["id"])
            signal_event_ids.append(signal_event_id)

            for metric in _report_metrics(row):
                await self._normalization_repository.upsert_signal_metric(
                    signal_event_id=signal_event_id,
                    **metric,
                )
            await self._normalization_repository.record_validation_log(
                target_type="signal_event",
                target_id_int=signal_event_id,
                validation_type="source_trace",
                passed=True,
                message=f"Normalized from raw_document_id={raw_document_id}",
            )

        analyze_task_id = None
        if signal_event_ids:
            analyze_task_id = await self._queue_repository.enqueue(
                stock_id=int(rows[0]["stock_id"]),
                task_type=ANALYZE_REPORT,
                priority="batch",
                source_signal_event_ids=signal_event_ids,
                task_context={
                    "stock_code": task_context.get("stock_code"),
                    "source_type": self.SOURCE_TYPE,
                    "run_key": "REPORT",
                },
                dedupe=True,
            )

        return {
            "normalized_count": len(rows),
            "signal_event_ids": signal_event_ids,
            "analyze_task_id": analyze_task_id,
        }


class ReportAnalyzeTaskHandler:
    """Persist deterministic Report valuation analysis from canonical events."""

    PROMPT_VER = "report-valuation-v1"

    def __init__(self, *, connection: Any) -> None:
        self._connection = connection
        self._analysis_repository = AnalysisRepository(connection)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        signal_event_ids = _source_signal_event_ids(task.get("source_signal_event_ids"))
        if not signal_event_ids:
            return {
                "analyzed_count": 0,
                "analysis_result_id": None,
                "agent_result_id": None,
                "skipped_reason": "source_signal_event_ids_required",
            }

        rows = await self._list_report_valuation_events(signal_event_ids)
        events = [dict(row) for row in rows]
        if not events:
            return {
                "analyzed_count": 0,
                "analysis_result_id": None,
                "agent_result_id": None,
                "skipped_reason": "report_events_not_found",
            }

        event_ids = [int(event["id"]) for event in events]
        analysis_date = _report_analysis_date(events, task_context)
        run_key = str(task_context.get("run_key") or "REPORT").strip() or "REPORT"
        direction = _report_analysis_direction(events)
        source_score = _report_source_score(events, direction)
        needs_review = any(bool(event.get("needs_review") or event.get("fact_needs_review")) for event in events)
        risk_flags = _report_risk_flags(events, needs_review)

        analysis_result = await self._analysis_repository.upsert_analysis_result(
            stock_id=stock_id,
            analysis_date=analysis_date,
            run_key=run_key,
            source_signal_event_ids=event_ids,
            base_score=_score_to_100(source_score),
            analysis_mode="full",
            warning="; ".join(risk_flags) or None,
            version=self.PROMPT_VER,
        )
        method_detail = {
            "source": "REPORT",
            "source_score": source_score,
            "summary": _report_analysis_summary(events, direction),
            "risk_flags": risk_flags,
            "needs_review": needs_review,
            "stock_code": task_context.get("stock_code") or _first_non_empty(events, "ticker"),
            "analysis_source": "rules",
            "report_quant": {
                "valuation": _report_valuation_payload(events),
            },
        }
        agent_result = await self._analysis_repository.upsert_agent_result(
            result_id=int(analysis_result["id"]),
            stock_id=stock_id,
            debate_method="D-1",
            source_signal_event_ids=event_ids,
            method_score=_score_to_100(source_score),
            method_signal=direction,
            method_detail=method_detail,
            reliability_score=65 if needs_review else 80,
            evidence_quality=_report_evidence_quality(events),
            llm_model=None,
            prompt_ver=self.PROMPT_VER,
        )

        return {
            "analysis_result_id": analysis_result["id"],
            "agent_result_id": agent_result["id"],
            "analyzed_count": len(events),
            "direction": direction,
            "score": source_score,
            "needs_review": needs_review,
            "analysis_source": "rules",
        }

    async def _list_report_valuation_events(self, signal_event_ids: list[int]) -> list[Any]:
        return list(await self._connection.fetch(
            """
            SELECT
                signal_events.id,
                signal_events.stock_id,
                signal_events.event_date,
                signal_events.signal_direction,
                signal_events.impact_level,
                signal_events.title,
                signal_events.summary,
                signal_events.evidence_url,
                signal_events.needs_review,
                source_documents.raw_document_id,
                report_valuation_facts.ticker,
                report_valuation_facts.broker,
                report_valuation_facts.publish_date,
                report_valuation_facts.target_price,
                report_valuation_facts.forward_eps_est,
                report_valuation_facts.eps_fy,
                report_valuation_facts.methodology,
                report_valuation_facts.applied_multiple,
                report_valuation_facts.implied_multiple,
                report_valuation_facts.peer_group,
                report_valuation_facts.category_tag,
                report_valuation_facts.rerating_thesis,
                report_valuation_facts.extraction_source,
                report_valuation_facts.needs_review AS fact_needs_review
            FROM signal_events
            INNER JOIN source_documents
                ON source_documents.id = signal_events.source_document_id
            LEFT JOIN report_valuation_facts
                ON report_valuation_facts.raw_document_id = source_documents.raw_document_id
            WHERE signal_events.id = ANY($1::BIGINT[])
              AND signal_events.source_type = 'REPORT'
            ORDER BY signal_events.event_date DESC, signal_events.id DESC
            """,
            signal_event_ids,
        ))


def _report_event_hash(raw_document_id: int) -> str:
    return hashlib.sha256(f"REPORT|{raw_document_id}".encode()).hexdigest()


def _report_signal_direction(opinion: Any) -> str:
    text = str(opinion or "").strip().lower()
    if not text:
        return "unknown"
    if any(token in text for token in ("buy", "매수", "outperform", "strong")):
        return "positive"
    if any(token in text for token in ("sell", "매도", "reduce", "underperform")):
        return "negative"
    if any(token in text for token in ("hold", "neutral", "marketperform", "중립")):
        return "neutral"
    return "unknown"


def _report_impact_level(row: Mapping[str, Any]) -> str:
    if row.get("target_price") is not None or row.get("upside_pct") is not None:
        return "medium"
    return "low"


def _report_needs_review(row: Mapping[str, Any]) -> bool:
    return _report_signal_direction(row.get("investment_opinion")) == "unknown"


def _report_summary(row: Mapping[str, Any]) -> str:
    firm = str(row.get("securities_firm") or row.get("source_name") or "증권사")
    return f"{firm} 리포트에서 확인된 데이터 방향성입니다. 원문 근거와 소스 간 일치도 확인이 필요합니다."


def _report_evidence_text(row: Mapping[str, Any]) -> str:
    parts = [
        f"증권사: {row.get('securities_firm')}" if row.get("securities_firm") else "",
        f"원천 리포트 의견: {row.get('investment_opinion')}" if row.get("investment_opinion") else "",
        f"목표가: {row.get('target_price')}" if row.get("target_price") is not None else "",
        str(row.get("key_rationale") or "").strip(),
        str(row.get("extracted_text") or "").strip()[:500],
    ]
    return "\n".join(part for part in parts if part)


def _report_metrics(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for column, metric_name, metric_unit in (
        ("target_price", "report_target_price", "KRW"),
        ("previous_target_price", "report_previous_target_price", "KRW"),
        ("current_price_at_publish", "report_current_price_at_publish", "KRW"),
        ("upside_pct", "report_upside_pct", "percent"),
    ):
        value = row.get(column)
        if value is None:
            continue
        metrics.append({
            "metric_name": metric_name,
            "metric_value": value,
            "metric_unit": metric_unit,
        })
    return metrics


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


async def _save_to_db(
    collection_repository: CollectionRepository,
    reports: list[dict],
    stock_id: int,
    *,
    collector_run_id: int | None = None,
) -> dict[str, Any]:
    """
    raw_documents + report_raw_details 동시 INSERT.
    트랜잭션 전략: 리포트 1건 = 트랜잭션 1개.
    source_hash UNIQUE 충돌 시 기존 id를 조회해 process_report 태스크를 재등록한다.
    """
    result = _new_report_collection_result()
    for report in reports:
        source_hash = _compute_source_hash(report, stock_id)
        publish_date = _parse_report_date(report.get("date", ""))
        if publish_date is None:
            result["invalid_date_reports"] += 1
            result["skip_reasons"]["invalid_date"] = result["skip_reasons"].get("invalid_date", 0) + 1
            continue

        pdf_url = report.get("pdf_direct_url") or report.get("pdf_url")
        if not pdf_url:
            result["missing_pdf_reports"] += 1

        row = await collection_repository.upsert_raw_document(
            stock_id=stock_id,
            collector_run_id=collector_run_id,
            source_type="REPORT",
            source_name=report.get("firm", ""),
            external_id=source_hash,
            source_hash=source_hash,
            title=report.get("title", ""),
            source_url=pdf_url,
            published_at=publish_date,
            collector_ver="1.0",
        )

        raw_document_id = row["id"]
        inserted = bool(row.get("inserted", True))
        if inserted:
            result["inserted_reports"] += 1
        else:
            result["duplicate_reports"] += 1

        await collection_repository.upsert_report_detail(
            raw_document_id=raw_document_id,
            stock_id=stock_id,
            securities_firm=report.get("firm", ""),
            publish_date=publish_date,
            has_pdf=bool(pdf_url),
            pdf_url=pdf_url,
            parsing_status="pending",
            extra_payload={"report_type": report.get("report_type")},
        )

        result["raw_document_ids"].append(raw_document_id)
        result["saved_reports"] += 1
    return result


def _new_report_collection_result() -> dict[str, Any]:
    return {
        "raw_document_ids": [],
        "saved_reports": 0,
        "inserted_reports": 0,
        "duplicate_reports": 0,
        "invalid_date_reports": 0,
        "missing_pdf_reports": 0,
        "skip_reasons": {},
    }


def _report_collection_log_payload(
    *,
    status: str,
    collector_run_id: int,
    stock_id: int,
    stock_code: str,
    reports: list[dict],
    save_result: Mapping[str, Any],
    enqueued_count: int,
    error_message: str | None = None,
) -> dict[str, Any]:
    payload = {
        "status": status,
        "collector_run_id": collector_run_id,
        "stock_id": stock_id,
        "stock_code": stock_code,
        "collected_reports": len(reports),
        "saved_reports": save_result["saved_reports"],
        "inserted_reports": save_result["inserted_reports"],
        "duplicate_reports": save_result["duplicate_reports"],
        "invalid_date_reports": save_result["invalid_date_reports"],
        "missing_pdf_reports": save_result["missing_pdf_reports"],
        "enqueued_reports": enqueued_count,
        "skip_reasons": save_result["skip_reasons"],
    }
    if error_message:
        payload["error_message"] = error_message
    return payload


def _compute_source_hash(report: dict, stock_id: int) -> str:
    """
    source_hash 규칙 (database/docs/source_hash_rule.md):
      REPORT|{stock_id}|{firm}|{title}|{publish_date}|{pdf_url}
    """
    parts = "|".join([
        "REPORT",
        str(stock_id),
        report.get("firm", ""),
        report.get("title", ""),
        str(report.get("date", "")),
        report.get("pdf_direct_url", "") or report.get("pdf_url", ""),
    ])
    return hashlib.sha256(parts.encode()).hexdigest()


def _parse_report_date(date_str: str) -> date | None:
    for fmt in ("%Y.%m.%d", "%y.%m.%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _task_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _source_raw_ids(value: Any) -> list[int]:
    return _int_list(value)


def _source_signal_event_ids(value: Any) -> list[int]:
    return _int_list(value)


def _int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        return [int(item) for item in value]
    if isinstance(value, tuple):
        return [int(item) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("{") and text.endswith("}"):
            inner = text[1:-1].strip()
            return [int(item.strip()) for item in inner.split(",") if item.strip()]
        parsed = json.loads(text)
        return [int(item) for item in parsed]
    return [int(value)]


def _extra_payload(value: Any) -> dict[str, Any]:
    """report_raw_details.extra_payload(JSONB)를 dict로 정규화.

    asyncpg는 JSONB 코덱을 등록하지 않으면 문자열로 돌려준다. str/dict/None 모두 수용.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value)


def _report_analysis_date(events: list[dict[str, Any]], task_context: dict[str, Any]) -> date:
    value = task_context.get("analysis_date") or task_context.get("event_date")
    if value:
        return _to_date(value)
    dates = [_to_date(event["event_date"]) for event in events if event.get("event_date")]
    return max(dates) if dates else date.today()


def _report_analysis_direction(events: list[dict[str, Any]]) -> str:
    weights = {"positive": 0, "negative": 0, "neutral": 0}
    for event in events:
        direction = str(event.get("signal_direction") or "unknown")
        if direction in weights:
            weights[direction] += 1
    if weights["positive"] > weights["negative"] and weights["positive"] >= weights["neutral"]:
        return "positive"
    if weights["negative"] > weights["positive"] and weights["negative"] >= weights["neutral"]:
        return "negative"
    if weights["positive"] and weights["negative"]:
        return "mixed"
    return "neutral"


def _report_source_score(events: list[dict[str, Any]], direction: str) -> float:
    base = {
        "positive": 0.35,
        "negative": -0.35,
        "mixed": 0.0,
        "neutral": 0.0,
    }.get(direction, 0.0)
    complete_facts = sum(1 for event in events if event.get("target_price") is not None and event.get("implied_multiple") is not None)
    completeness_bonus = min(0.15, complete_facts * 0.05)
    review_penalty = 0.15 if any(bool(event.get("needs_review") or event.get("fact_needs_review")) for event in events) else 0.0
    if base < 0:
        return round(max(-1.0, base - completeness_bonus + review_penalty), 4)
    return round(max(-1.0, min(1.0, base + completeness_bonus - review_penalty)), 4)


def _report_risk_flags(events: list[dict[str, Any]], needs_review: bool) -> list[str]:
    flags: list[str] = []
    if needs_review:
        flags.append("valuation_review_required")
    if any(event.get("target_price") is None for event in events):
        flags.append("target_price_missing")
    if any(event.get("implied_multiple") is None for event in events):
        flags.append("implied_multiple_missing")
    return flags


def _report_analysis_summary(events: list[dict[str, Any]], direction: str) -> str:
    brokers = sorted({str(event.get("broker") or "").strip() for event in events if event.get("broker")})
    broker_text = ", ".join(brokers[:3]) if brokers else "증권사 리포트"
    direction_text = {
        "positive": "긍정 방향",
        "negative": "주의 방향",
        "mixed": "혼재",
        "neutral": "중립",
    }.get(direction, "추가 확인 필요")
    return f"{broker_text} 자료의 밸류에이션 fact 기준 데이터 방향성은 {direction_text}입니다. 소스 간 일치도와 원문 근거 확인이 필요합니다."


def _report_valuation_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    latest = events[0]
    implied_values = [_float_or_none(event.get("implied_multiple")) for event in events]
    implied_values = [value for value in implied_values if value is not None]
    return {
        "target_price": _json_ready(latest.get("target_price")),
        "forward_eps_est": _json_ready(latest.get("forward_eps_est")),
        "eps_fy": _json_ready(latest.get("eps_fy")),
        "methodology": latest.get("methodology") or "unknown",
        "applied_multiple": _json_ready(latest.get("applied_multiple")),
        "implied_multiple": _json_ready(latest.get("implied_multiple")),
        "implied_multiple_avg": round(sum(implied_values) / len(implied_values), 4) if implied_values else None,
        "peer_group": _json_ready(_peer_group(latest.get("peer_group"))),
        "category_tag": latest.get("category_tag"),
        "rerating_thesis": latest.get("rerating_thesis"),
        "extraction_source": latest.get("extraction_source") or "rules",
        "needs_review": bool(latest.get("needs_review") or latest.get("fact_needs_review")),
        "event_count": len(events),
    }


def _report_evidence_quality(events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    fact_count = sum(1 for event in events if event.get("raw_document_id") is not None)
    return round((fact_count / len(events)) * 100)


def _score_to_100(score: float) -> float:
    return round(max(0.0, min(100.0, (score + 1.0) * 50.0)), 2)


def _first_non_empty(events: list[dict[str, Any]], key: str) -> Any:
    for event in events:
        value = event.get(key)
        if value not in (None, ""):
            return value
    return None


def _peer_group(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return parsed if isinstance(parsed, list) else [parsed]
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if hasattr(value, "as_tuple"):
        number = float(value)
        return int(number) if number.is_integer() else number
    return value
