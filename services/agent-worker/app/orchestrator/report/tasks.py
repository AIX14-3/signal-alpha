from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from app.agents.base import SourceAgentInput
from app.agents.report.agent import ReportAnalysisAgent
from app.analyzers.report.rag_retriever import ReportRagRetriever
from app.collectors.report.crawler import collect_stock
from app.collectors.report.parsers.chunker import chunk_text
from app.collectors.report.parsers.pdf_extractor import extract_text
from app.collectors.report.parsers.run_parser import process_from_s3
from app.collectors.report.pdf_downloader import download_and_upload, make_filename, make_s3_key
from app.collectors.report.storage import ReportStorageClient, get_report_storage_client
from app.embeddings.provider import get_embedding_provider, to_pgvector
from app.orchestrator.queue.task_types import (
    ANALYZE_REPORT,
    EMBED_REPORT,
    ML_INFER,
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
        saved_ids: list[int] = []
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

            saved_ids = await _save_to_db(
                collection_repository,
                reports,
                stock_id,
                collector_run_id=collector_run_id,
            )

            queue = ProcessingQueueRepository(self._connection)
            for raw_document_id in saved_ids:
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
        except Exception as exc:
            await collection_repository.finish_collector_run(
                run_id=collector_run_id,
                status="failed",
                collected_count=len(reports),
                inserted_count=len(saved_ids),
                skipped_count=max(0, len(reports) - len(saved_ids)),
                failed_count=1,
                error_message=str(exc),
            )
            raise

        await collection_repository.finish_collector_run(
            run_id=collector_run_id,
            status="success",
            collected_count=len(reports),
            inserted_count=len(saved_ids),
            skipped_count=max(0, len(reports) - len(saved_ids)),
            failed_count=0,
        )

        return {"collector_run_id": collector_run_id, "collected": len(saved_ids)}


class ReportProcessTaskHandler:
    """
    1. report_raw_details에서 pdf_url, s3_key 확인
    2. PDF 다운로드 → report storage 업로드 (s3_key 미존재 시)
    3. LLM 파싱 (bytes 직접 처리, tempfile 없음)
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
        filename = make_filename({
            "firm": row["securities_firm"],
            "date": str(row["publish_date"]).replace("-", "."),
            "report_type": extra.get("report_type") or "cr",
        })
        s3_key = row["s3_key"] or make_s3_key(str(row["stock_code"]), filename)

        if not row["pdf_url"]:
            await self._mark_failed(raw_document_id, "pdf_url이 없습니다")
            return {"status": "no_pdf_url"}

        storage = self._get_storage()

        if not storage.exists(s3_key):
            success = download_and_upload(row["pdf_url"], s3_key, storage)
            if not success:
                await self._mark_failed(raw_document_id, "PDF 다운로드 실패")
                return {"status": "download_failed"}

        parsed = process_from_s3(s3_key, storage)

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

        # 파싱 완료 → 임베딩 태스크 자동 등록. 모델(~2GB) 싱글톤 1회 로딩을 위해
        # 임베딩은 별도 embed_report 태스크로 분리한다(파싱 실패와 격리, 독립 재시도).
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
        embed_task_ids: list[int] = []
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
            embed_task_id = await self._queue_repository.enqueue(
                stock_id=stock_id,
                task_type=EMBED_REPORT,
                priority="batch",
                source_raw_ids=[raw_document_id],
                source_signal_event_ids=[signal_event_id],
                task_context={
                    "raw_document_id": raw_document_id,
                    "stock_code": task_context.get("stock_code"),
                    "source_type": self.SOURCE_TYPE,
                    "run_key": f"REPORT_EVENT_{signal_event_id}",
                },
                dedupe=True,
            )
            embed_task_ids.append(embed_task_id)

        return {
            "normalized_count": len(rows),
            "signal_event_ids": signal_event_ids,
            "embed_task_ids": embed_task_ids,
        }


class ReportEmbedTaskHandler:
    """
    1. report_raw_details에서 s3_key 확인 (parsing_status='success' 인 문서만)
    2. report storage PDF **전문** 추출 → 청킹 (앞 3p 아님 — 검색 품질을 위해 문서 전체)
    3. BGE-M3(1024d) 임베딩 → report_chunks(canonical) 적재
       UNIQUE(raw_document_id, chunk_index) → 재실행 시 ON CONFLICT DO NOTHING으로 멱등.
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
                   s.ticker AS stock_code,
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
        if row["parsing_status"] != "success" or not row["s3_key"]:
            # 아직 파싱 전이거나 report storage에 PDF가 없음 → 임베딩 불가
            return {"status": "not_ready", "raw_document_id": raw_document_id}

        storage = self._get_storage()
        pdf_bytes = storage.download_pdf(row["s3_key"])
        full_text = extract_text(pdf_bytes)
        chunks = chunk_text(full_text)
        if not chunks:
            return {"status": "no_text", "raw_document_id": raw_document_id}

        embeddings = await get_embedding_provider().embed(chunks)

        stock_id = int(row["stock_id"])
        for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            await self._connection.execute(
                """
                INSERT INTO report_chunks (
                    raw_document_id, stock_id, chunk_index,
                    chunk_text, token_count, embedding
                )
                VALUES ($1, $2, $3, $4, $5, $6::vector)
                ON CONFLICT (raw_document_id, chunk_index) DO NOTHING
                """,
                raw_document_id,
                stock_id,
                idx,
                chunk,
                len(chunk.split()),
                to_pgvector(embedding),
            )

        return {
            "status": "success",
            "raw_document_id": raw_document_id,
            "chunks": len(chunks),
            "analyze_task_id": await self._enqueue_analysis(task, row, raw_document_id),
        }

    def _get_storage(self) -> ReportStorageClient:
        if self._storage is None:
            self._storage = get_report_storage_client(self._settings)
            self._s3 = self._storage
        return self._storage

    async def _enqueue_analysis(
        self,
        task: Mapping[str, Any],
        row: Mapping[str, Any],
        raw_document_id: int,
    ) -> int | None:
        signal_event_ids = _source_signal_event_ids(task.get("source_signal_event_ids"))
        if not signal_event_ids:
            return None
        task_context = _task_context(task.get("task_context"))
        stock_code = task_context.get("stock_code") or row.get("stock_code")
        run_key = str(task_context.get("run_key") or f"REPORT_EVENT_{signal_event_ids[0]}")
        queue = ProcessingQueueRepository(self._connection)
        return await queue.enqueue(
            stock_id=int(row["stock_id"]),
            task_type=ANALYZE_REPORT,
            priority="batch",
            source_raw_ids=[raw_document_id],
            source_signal_event_ids=signal_event_ids,
            task_context={
                "raw_document_id": raw_document_id,
                "stock_code": stock_code,
                "source_type": "REPORT",
                "run_key": run_key,
            },
            dedupe=True,
        )


class ReportAnalyzeTaskHandler:
    """
    RAG 입력 구성 → ReportAnalysisAgent 호출 → canonical 저장(DART analyze 패턴).

    - 정량 메타(report_quant)는 핸들러가 report_raw_details에서 조회해 context로 주입.
    - RAG 검색은 agent가 주입된 ReportRagRetriever로 수행(agent는 DB 직접 접근 안 함).
    - 저장: analysis_results + agent_results. final_signals는 다중 소스 집계 단계 소관이라 여기서 안 씀.
    """

    def __init__(
        self,
        *,
        connection: Any,
        settings: Any,
        llm_client: Any = None,
        llm_model: str | None = None,
        llm_timeout_seconds: float = 20.0,
        analysis_agent: Any = None,
    ) -> None:
        self._connection = connection
        self._normalization_repository = NormalizationRepository(connection)
        self._analysis_repository = AnalysisRepository(connection)
        self._queue_repository = ProcessingQueueRepository(connection)
        self._agent = analysis_agent or ReportAnalysisAgent(
            retriever=ReportRagRetriever(connection),
            llm_client=llm_client,
            llm_model=llm_model,
            timeout_seconds=llm_timeout_seconds,
        )

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        stock_code = str(task_context.get("stock_code") or "")
        run_key = str(task_context.get("run_key") or "REPORT").strip() or "REPORT"
        signal_event_ids = _source_signal_event_ids(task.get("source_signal_event_ids"))
        if not signal_event_ids:
            signal_event_ids = _source_signal_event_ids(task_context.get("source_signal_event_ids"))
        events = [
            dict(row)
            for row in await self._normalization_repository.list_signal_events_by_ids(
                signal_event_ids
            )
        ] if signal_event_ids else []
        signal_event_ids = [int(event["id"]) for event in events]
        analysis_date = _report_analysis_date(task_context, events)

        quant = await self._build_quant(stock_id)

        result = await self._agent.analyze(
            SourceAgentInput(
                source="REPORT",
                stock_code=stock_code,
                stock_id=stock_id,
                analysis_date=analysis_date,
                run_key=run_key,
                events=events,
                context={"report_quant": quant},
            )
        )

        analysis_result = await self._analysis_repository.upsert_analysis_result(
            stock_id=stock_id,
            analysis_date=analysis_date,
            run_key=run_key,
            source_signal_event_ids=signal_event_ids,
            base_score=_report_score_100(result.score),
            analysis_mode="quick",
            warning="; ".join(result.risk_flags) or None,
            version=result.prompt_ver,
        )
        agent_result = await self._analysis_repository.upsert_agent_result(
            result_id=analysis_result["id"],
            stock_id=stock_id,
            debate_method="D-1",
            source_signal_event_ids=signal_event_ids,
            method_score=_report_score_100(result.score),
            method_signal=_method_signal(result.direction),
            method_detail={
                **result.method_detail,
                "source": "REPORT",
                "source_type": "REPORT",
                "direction": result.direction,
                "source_score": _report_source_score(result.score),
                "summary": result.summary,
                "risk_flags": result.risk_flags,
                "needs_review": result.needs_review,
                "data_status": result.data_status,
                "stock_code": stock_code,
                "analysis_source": result.analysis_source,
                **({"llm_error": result.llm_error} if result.llm_error else {}),
            },
            # 3사 한정 표본 → DART(90)보다 신뢰도 보수적(결정 B).
            reliability_score=70,
            evidence_quality=len(result.method_detail.get("evidence_chunks", [])),
            llm_model=result.llm_model,
            prompt_ver=result.prompt_ver,
        )
        ml_infer_task_id = await self._enqueue_ml_infer(
            stock_id=stock_id,
            stock_code=stock_code,
            analysis_date=analysis_date,
            analysis_result_id=int(analysis_result["id"]),
        ) if signal_event_ids else None
        return {
            "analysis_result_id": analysis_result["id"],
            "agent_result_id": agent_result["id"],
            "ml_infer_task_id": ml_infer_task_id,
            "direction": result.direction,
            "score": result.score,
            "needs_review": result.needs_review,
            "analysis_source": result.analysis_source,
            "report_quant": quant,
        }

    async def _enqueue_ml_infer(
        self,
        *,
        stock_id: int,
        stock_code: str,
        analysis_date: date,
        analysis_result_id: int,
    ) -> int:
        aggregate_ctx = {
            "stock_code": stock_code,
            "signal_date": analysis_date.isoformat(),
            "run_key": "AGGREGATED",
            "aggregation_key": f"AGGREGATED:{stock_id}:{analysis_date.isoformat()}:final-agg-v1",
            "source_analysis_result_ids": [analysis_result_id],
        }
        return await self._queue_repository.enqueue(
            stock_id=stock_id,
            task_type=ML_INFER,
            priority="batch",
            source_analysis_result_ids=[analysis_result_id],
            task_context={
                "stock_code": stock_code,
                "run_key": "ML",
                "aggregate_ctx": aggregate_ctx,
            },
            dedupe=True,
        )

    async def _build_quant(self, stock_id: int) -> dict[str, Any]:
        """파싱 완료된 리포트에서 목표주가 평균·투자의견 분포·의견 충돌 여부를 집계."""
        rows = await self._connection.fetch(
            """
            SELECT investment_opinion, target_price
            FROM report_raw_details
            WHERE stock_id = $1 AND parsing_status = 'success'
            """,
            stock_id,
        )
        targets = [int(r["target_price"]) for r in rows if r["target_price"] is not None]
        opinions: dict[str, int] = {}
        for r in rows:
            opinion = str(r["investment_opinion"] or "").strip()
            if opinion:
                opinions[opinion] = opinions.get(opinion, 0) + 1
        return {
            "report_count": len(rows),
            "avg_target": round(sum(targets) / len(targets)) if targets else None,
            "opinions": [
                {"opinion": k, "count": v} for k, v in sorted(opinions.items())
            ],
            # 표본 작아 통계적 신뢰는 낮지만, 서로 다른 의견이 섞이면 conflict로 표시.
            "conflict_detected": len(opinions) > 1,
        }


def _method_signal(direction: str) -> str:
    """agent_results.method_signal CHECK는 positive/negative/neutral/mixed만 허용한다.
    agent는 fallback/판정불가 시 'unknown'을 내므로 DART(_agent_signal)와 동일하게 neutral로 매핑.
    """
    return direction if direction in {"positive", "negative", "neutral", "mixed"} else "neutral"


def _report_analysis_date(task_context: dict[str, Any], events: list[dict[str, Any]] | None = None) -> date:
    value = task_context.get("analysis_date")
    if value:
        return _to_date(value)
    event_dates = [_to_date(event["event_date"]) for event in events or [] if event.get("event_date")]
    return max(event_dates) if event_dates else date.today()


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


def _report_score_100(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 50.0
    return max(0.0, min(100.0, number))


def _report_source_score(value: Any) -> float:
    return max(-1.0, min(1.0, (_report_score_100(value) / 50.0) - 1.0))


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
) -> list[int]:
    """
    raw_documents + report_raw_details 동시 INSERT.
    트랜잭션 전략: 리포트 1건 = 트랜잭션 1개.
    source_hash UNIQUE 충돌 시 기존 id를 조회해 process_report 태스크를 재등록한다.
    """
    saved_ids: list[int] = []
    for report in reports:
        source_hash = _compute_source_hash(report, stock_id)
        publish_date = _parse_report_date(report.get("date", ""))
        if publish_date is None:
            continue

        pdf_url = report.get("pdf_direct_url") or report.get("pdf_url")
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

        saved_ids.append(raw_document_id)
    return saved_ids


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
