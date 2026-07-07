from __future__ import annotations

import asyncio
import functools
import json
import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Callable, TypeVar

from app.collectors.report.crawler import collect_stock
from app.collectors.report.parsers.run_parser import process_from_s3
from app.collectors.report.pdf_downloader import download_and_upload, make_report_storage_key
from app.collectors.report.storage import ReportStorageClient, get_report_storage_client
from app.orchestrator.queue.context import enqueue_aggregate
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
from app.orchestrator.report._report_helpers import (
    _compute_source_hash,
    _extra_payload,
    _first_non_empty,
    _new_report_collection_result,
    _parse_report_date,
    _report_analysis_date,
    _report_analysis_summary,
    _report_collection_log_payload,
    _report_consensus_direction,
    _report_event_hash,
    _report_evidence_quality,
    _report_evidence_text,
    _report_impact_level,
    _report_metrics,
    _report_needs_review,
    _report_risk_flags,
    _report_signal_direction,
    _report_summary,
    _report_valuation_payload,
    _score_to_100,
    _source_raw_ids,
    _source_signal_event_ids,
    _task_context,
    _to_date,
)

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


async def _run_blocking(fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """동기 블로킹 호출(requests/PDF 파싱/time.sleep 크롤)을 기본 스레드풀로 오프로딩한다(H4).

    이 핸들러들은 단일 이벤트 루프에서 돌므로, 블로킹 IO 를 직접 await 경로에서 호출하면 그 수십 초
    동안 다른 큐 작업·HTTP·데몬이 전부 동결된다. ``run_in_executor`` 로 워커 스레드에 넘겨 루프를 비운다.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


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

            reports = await _run_blocking(
                collect_stock,
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

        if not await _run_blocking(storage.exists, s3_key):
            success = await _run_blocking(download_and_upload, row["pdf_url"], s3_key, storage)
            if not success:
                await self._mark_failed(raw_document_id, "PDF 다운로드 실패")
                return {"status": "download_failed"}

        try:
            parsed = await _run_blocking(process_from_s3, s3_key, storage, settings=self._settings)
        except Exception as exc:  # noqa: BLE001 — fitz/파서 실패를 큐 전체 실패로 번지지 않게 격리
            logger.warning(
                "report parse failed raw_document_id=%s s3_key=%s: %s",
                raw_document_id,
                s3_key,
                exc,
            )
            await self._mark_failed(raw_document_id, "parse_failed")
            return {"status": "parse_failed"}

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

        embed_task_id = None
        return {
            "status": "success",
            "raw_document_id": raw_document_id,
            "s3_key": s3_key,
            "normalize_task_id": normalize_task_id,
            "embed_task_id": embed_task_id,
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

    def __init__(
        self,
        *,
        connection: Any,
        settings: Any | None = None,
    ) -> None:
        self._connection = connection
        self._settings = settings
        self._analysis_repository = AnalysisRepository(connection)
        self._queue_repository = ProcessingQueueRepository(connection)

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
        # 결정론 밸류에이션 신호: 정규화가 채운 애널리스트 투자의견(signal_direction)의 컨센서스로
        # 방향/점수를 낸다(현재가 불필요 — 의견 분포만 사용). 의견 데이터가 전혀 없으면 features-only
        # 폴백(unknown/no_signal). 점수는 SCORING_SOURCES 에 REPORT 가 없어 final_score 에 산입되지
        # 않고(점수=주가/집계), 방향은 소스 근거로 쓰인다(소스별 라우팅: REPORT→결정론 밸류+LLM 서술).
        direction, source_score, data_status = _report_consensus_direction(events)
        needs_review = any(bool(event.get("needs_review") or event.get("fact_needs_review")) for event in events)
        risk_flags = _report_risk_flags(events, needs_review)  # 데이터 품질 플래그

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
            "data_status": data_status,  # 의견 컨센서스 있으면 ok, 없으면 no_signal(폴백)
            "stock_code": task_context.get("stock_code") or _first_non_empty(events, "ticker"),
            "analysis_source": "rules" if data_status == "ok" else "features",
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
        aggregate_ctx = {
            "stock_code": task_context.get("stock_code") or _first_non_empty(events, "ticker"),
            "signal_date": analysis_date.isoformat(),
            "run_key": "AGGREGATED",
            "aggregation_key": f"AGGREGATED:{stock_id}:{analysis_date.isoformat()}:final-agg-v1",
            "source_analysis_result_ids": [int(analysis_result["id"])],
        }
        aggregate_task_id = await enqueue_aggregate(
            self._queue_repository,
            stock_id=stock_id,
            aggregate_ctx=aggregate_ctx,
            priority=str(task_context.get("priority") or "batch"),
        )

        return {
            "analysis_result_id": analysis_result["id"],
            "agent_result_id": agent_result["id"],
            "aggregate_task_id": aggregate_task_id,
            "analyzed_count": len(events),
            "direction": direction,
            "score": source_score,
            "needs_review": needs_review,
            "analysis_source": "rules" if data_status == "ok" else "features",
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
