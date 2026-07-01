from __future__ import annotations

import asyncio
import copy
import functools
import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any, Callable, TypeVar

from app.agents.base import SourceAgentInput
from app.clients.embedding_client import EmbeddingError, GeminiEmbeddingClient
from app.clients.pgvector import to_pgvector
from app.collectors.report.crawler import collect_stock
from app.collectors.report.parsers.run_parser import process_from_s3
from app.collectors.report.pdf_downloader import download_and_upload, make_report_storage_key
from app.collectors.report.storage import ReportStorageClient, get_report_storage_client
from app.orchestrator.queue.context import enqueue_aggregate
from app.orchestrator.queue.task_types import (
    ANALYZE_REPORT,
    EMBED_REPORT,
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

        parsed = await _run_blocking(process_from_s3, s3_key, storage, settings=self._settings)

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

        # RAG 임베딩(Tier A) — REPORT_USE_LLM=true 일 때만 사이드 태스크로 인큐. 결정론 점수 경로
        # (normalize→analyze)와 분리돼 임베딩 실패가 점수 산출을 막지 않는다. 기본 OFF → 동작 무변화.
        # NORMALIZE 가 아니라 여기(PROCESS)서 인큐한다 — 정규화는 임베딩을 기다리지 않는다.
        embed_task_id = None
        if getattr(self._settings, "report_use_llm", False):
            embed_task_id = await queue.enqueue(
                stock_id=int(row["stock_id"]),
                task_type=EMBED_REPORT,
                priority="batch",
                source_raw_ids=[raw_document_id],
                task_context={
                    "raw_document_id": raw_document_id,
                    "stock_code": str(row["stock_code"]),
                },
                dedupe=True,
            )

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


_MAX_CHUNKS_PER_DOC = 80


def _rows_affected(tag: Any) -> int:
    """asyncpg execute() 태그(예: 'INSERT 0 1' / 'INSERT 0 0')에서 실제 반영 행수를 파싱.

    ON CONFLICT DO NOTHING 으로 스킵되면 마지막 숫자가 0 이라 실제 적재분만 센다. 태그 형식이
    예상과 다르면(모의 커넥션 등) 보수적으로 1 로 간주한다.
    """
    try:
        return int(str(tag).split()[-1])
    except (ValueError, IndexError, TypeError):
        return 1


def _chunk_report_text(text: str) -> list[str]:
    """Lazy wrapper so ``report.tasks`` import stays free of langchain (chunker dep).

    ``chunk_text`` pulls in ``langchain_text_splitters``; importing it at module top would
    load langchain into every consumer of this widely-imported module (handlers → whole
    worker) just for the optional embed side-task. Import on first use instead. Tests
    patch this wrapper.
    """
    from app.collectors.report.parsers.chunker import chunk_text

    return chunk_text(text)


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Lazy wrapper so ``report.tasks`` import stays free of PyMuPDF (fitz dep)."""
    from app.collectors.report.parsers.pdf_extractor import extract_text

    return extract_text(pdf_bytes)


class ReportEmbedTaskHandler:
    """Chunk a parsed report's full text, embed it, and load ``report_chunks`` (#709 RAG).

    Side path off the deterministic score pipeline: enqueued from ``PROCESS_REPORT`` only
    when ``REPORT_USE_LLM=true``, and it enqueues nothing downstream. Any failure (PDF
    gone, embedding API down) returns a status instead of raising, so the score path —
    which runs independently via NORMALIZE→ANALYZE — is never blocked.

    Text source (option 2): re-extracts the FULL PDF text from report storage. The
    persisted ``report_raw_details.extracted_text`` is only a 2000-char preview (too thin
    for RAG); the full text is re-extracted from the stored PDF, falling back to the
    preview only when the PDF is unavailable.
    """

    def __init__(
        self,
        *,
        connection: Any,
        settings: Any,
        storage: ReportStorageClient | None = None,
        embedder: Any | None = None,
    ) -> None:
        self._connection = connection
        self._settings = settings
        self._storage = storage
        self._embedder = embedder

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        task_context = _task_context(task.get("task_context"))
        raw_document_id = int(task_context["raw_document_id"])

        row = await self._connection.fetchrow(
            """
            SELECT rrd.raw_document_id,
                   rrd.s3_key,
                   rrd.extracted_text,
                   rrd.parsing_status,
                   rd.stock_id
            FROM report_raw_details rrd
            JOIN raw_documents rd ON rd.id = rrd.raw_document_id
            WHERE rrd.raw_document_id = $1
            """,
            raw_document_id,
        )
        if row is None:
            return {"status": "not_found", "raw_document_id": raw_document_id}
        if row["parsing_status"] != "success":
            return {"status": "not_parsed", "raw_document_id": raw_document_id}

        # 멱등: 이미 청크가 있으면 재실행은 no-op(재임베딩하려면 청크를 먼저 삭제).
        existing = await self._connection.fetchval(
            "SELECT 1 FROM report_chunks WHERE report_raw_detail_id = $1 LIMIT 1",
            raw_document_id,
        )
        if existing:
            return {"status": "already_embedded", "raw_document_id": raw_document_id}

        text = await self._resolve_text(row)
        chunks = _chunk_report_text(text)[:_MAX_CHUNKS_PER_DOC] if text.strip() else []
        if not chunks:
            return {"status": "no_text", "raw_document_id": raw_document_id}

        try:
            # 임베더 구성도 try 안에서 — GEMINI_API_KEY 미설정 시 EmbeddingError 를 여기서 삼킨다.
            embedder = self._embedder or GeminiEmbeddingClient()
            vectors = await embedder.embed_batch(chunks)
        except EmbeddingError as exc:
            # 일시 오류 내성: 청크 없이 남겨두고 재인큐 가능. 점수 경로로 예외 전파 금지.
            logger.warning(
                "report_embed_failed raw_document_id=%s error=%s", raw_document_id, exc
            )
            return {"status": "embed_error", "raw_document_id": raw_document_id, "error": str(exc)}

        # 부분 적재 방지: 전 청크를 한 트랜잭션으로 커밋 → 중간 크래시 시 0 으로 롤백돼 멱등
        # pre-check(청크 존재 여부)가 항상 정확하게 유지된다(부분 적재 후 재실행 시 나머지 유실 방지).
        inserted = 0
        async with self._connection.transaction():
            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                tag = await self._connection.execute(
                    """
                    INSERT INTO report_chunks
                        (report_raw_detail_id, chunk_index, chunk_text, embedding, token_count)
                    VALUES ($1, $2, $3, $4::vector, $5)
                    ON CONFLICT (report_raw_detail_id, chunk_index) DO NOTHING
                    """,
                    raw_document_id,
                    index,
                    chunk,
                    to_pgvector(vector),
                    len(chunk.split()),
                )
                inserted += _rows_affected(tag)

        # chunks=실제 적재 행수(ON CONFLICT 로 스킵된 건 제외), attempted=시도 청크 수.
        return {
            "status": "success",
            "raw_document_id": raw_document_id,
            "chunks": inserted,
            "attempted": len(chunks),
        }

    async def _resolve_text(self, row: Mapping[str, Any]) -> str:
        """전체 PDF 본문(옵션2 재추출); PDF 없거나 실패 시 2000자 프리뷰로 폴백."""
        s3_key = row.get("s3_key")
        if s3_key:
            try:
                storage = self._get_storage()
                pdf_bytes = await _run_blocking(storage.download_pdf, s3_key)
                text = await _run_blocking(_extract_pdf_text, pdf_bytes)
                if text and text.strip():
                    return text
            except Exception as exc:  # noqa: BLE001 — 프리뷰로 격하, 태스크는 절대 실패시키지 않음
                logger.warning(
                    "report_embed_pdf_reextract_failed raw_document_id=%s error=%s",
                    row.get("raw_document_id"),
                    exc,
                )
        return str(row.get("extracted_text") or "")

    def _get_storage(self) -> ReportStorageClient:
        if self._storage is None:
            self._storage = get_report_storage_client(self._settings)
        return self._storage


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
        agent: Any | None = None,
    ) -> None:
        self._connection = connection
        self._settings = settings
        self._agent = agent
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
        # LLM 재해석(Tier A, RAG) — REPORT_USE_LLM=true 일 때만. 결정론 방향/점수는 이미 확정됐고,
        # 에이전트 산출물은 오직 신규 method_detail["report_rag"] 사이드카에만 담는다. 기존 피처 키·
        # method_score/method_signal 은 무변경(ML 가드레일 — Wave 3 메타러너가 그대로 읽는다).
        # 어떤 실패도 결정론 write 를 막지 않는다(사이드카에 error 상태로만 기록).
        if getattr(self._settings, "report_use_llm", False):
            method_detail["report_rag"] = await self._reinterpret(
                stock_id=stock_id,
                stock_code=method_detail["stock_code"],
                direction=direction,
                source_score=source_score,
                report_quant=method_detail["report_quant"],
            )
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

    async def _reinterpret(
        self,
        *,
        stock_id: int,
        stock_code: Any,
        direction: str,
        source_score: float,
        report_quant: dict[str, Any],
    ) -> dict[str, Any]:
        """RAG 에이전트로 재해석/근거만 산출 → report_rag 사이드카(dict) 반환.

        결정론 점수/방향은 context 로 넘겨 에이전트가 그대로 echo 하게 하고(자체 산출 금지),
        여기서는 summary/risk_flags/evidence/needs_review 만 사이드카에 담는다. 에이전트 구성·
        실행 중 어떤 예외도 삼켜 error 상태로 기록 — 결정론 write 경로는 절대 깨지 않는다.
        """
        from app.agents.report.agent import ReportAnalysisAgent

        try:
            agent = self._agent or ReportAnalysisAgent(connection=self._connection)
            out = await agent.analyze(
                SourceAgentInput(
                    source="REPORT",
                    stock_code=str(stock_code or ""),
                    stock_id=stock_id,
                    context={
                        # deep copy — 에이전트가 실수로 in-place 변경해도 지속되는 피처 키(report_quant)를
                        # 훼손하지 못하게 격리(ML 가드레일 방어). 에이전트는 읽기 전용으로만 쓴다.
                        "report_quant": copy.deepcopy(report_quant),
                        "direction": direction,
                        "source_score": source_score,
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001 — 결정론 write 를 절대 깨지 않는다.
            logger.warning("report_rag_reinterpret_failed stock_id=%s error=%s", stock_id, exc)
            return {"status": "error", "error": str(exc)}

        rag = out.method_detail.get("report_rag", {}) if isinstance(out.method_detail, dict) else {}
        return {
            "summary": out.summary,
            "risk_flags": list(out.risk_flags),
            "evidence": rag.get("evidence", []),
            "needs_review": bool(out.needs_review),
            "analysis_source": out.analysis_source,
            "llm_model": out.llm_model,
            "prompt_ver": out.prompt_ver,
            "llm_error": out.llm_error,
            "status": rag.get("status", "ok"),
        }


def _report_event_hash(raw_document_id: int) -> str:
    return hashlib.sha256(f"REPORT|{raw_document_id}".encode()).hexdigest()


def _report_consensus_direction(events: list[dict[str, Any]]) -> tuple[str, float, str]:
    """애널리스트 투자의견 컨센서스로 (direction, score[-1,1], data_status) 산출 — 결정론.

    각 이벤트의 ``signal_direction``(정규화가 투자의견에서 매핑: positive/negative/neutral/unknown)을
    모아 순매수도(=(긍정−부정)/방향성건수)를 점수로 쓴다. 방향성 의견이 하나도 없으면 features-only
    폴백(unknown/0/no_signal) — 회귀 없음. 임계 ±0.2 는 AGGREGATE 의 방향 판정과 정렬한다.
    """
    directions = [str(event.get("signal_direction") or "unknown").strip().lower() for event in events]
    directional = [d for d in directions if d in {"positive", "negative", "neutral"}]
    if not directional:
        return "unknown", 0.0, "no_signal"
    positive = directional.count("positive")
    negative = directional.count("negative")
    score = round((positive - negative) / len(directional), 3)
    if score >= 0.2:
        direction = "positive"
    elif score <= -0.2:
        direction = "negative"
    else:
        direction = "neutral"
    return direction, score, "ok"


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


# Phase 0 (#525): _report_analysis_direction / _report_source_score(결정론 판정·스코어)는
# 제거됐다. 방향/점수는 학습형 메타러너 return 채널이 산출한다. 아래는 데이터 품질 플래그·요약만.


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
