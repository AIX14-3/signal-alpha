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
from app.collectors.report.s3_client import ReportS3Client
from app.embeddings.provider import get_embedding_provider, to_pgvector
from app.orchestrator.queue.task_types import EMBED_REPORT, PROCESS_REPORT
from signal_alpha_data_access.repositories import AnalysisRepository, ProcessingQueueRepository


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

        saved_ids = await _save_to_db(self._connection, reports, stock_id)

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

        return {"collected": len(saved_ids)}


class ReportProcessTaskHandler:
    """
    1. report_raw_details에서 pdf_url, s3_key 확인
    2. PDF 다운로드 → S3 업로드 (s3_key 미존재 시)
    3. LLM 파싱 (bytes 직접 처리, tempfile 없음)
    4. report_raw_details 업데이트 (parsing_status='success', target_price 등)
    """

    def __init__(self, *, connection: Any, settings: Any) -> None:
        self._connection = connection
        self._s3 = ReportS3Client()

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

        if not self._s3.exists(s3_key):
            success = download_and_upload(row["pdf_url"], s3_key, self._s3)
            if not success:
                await self._mark_failed(raw_document_id, "PDF 다운로드 실패")
                return {"status": "download_failed"}

        parsed = process_from_s3(s3_key, self._s3)

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
        await queue.enqueue(
            stock_id=int(row["stock_id"]),
            task_type=EMBED_REPORT,
            priority="batch",
            source_raw_ids=[raw_document_id],
            task_context={"raw_document_id": raw_document_id},
            dedupe=True,
        )

        return {"status": "success", "raw_document_id": raw_document_id, "s3_key": s3_key}

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


class ReportEmbedTaskHandler:
    """
    1. report_raw_details에서 s3_key 확인 (parsing_status='success' 인 문서만)
    2. S3 PDF **전문** 추출 → 청킹 (앞 3p 아님 — 검색 품질을 위해 문서 전체)
    3. BGE-M3(1024d) 임베딩 → report_chunks(canonical) 적재
       UNIQUE(raw_document_id, chunk_index) → 재실행 시 ON CONFLICT DO NOTHING으로 멱등.
    """

    def __init__(self, *, connection: Any, settings: Any) -> None:
        self._connection = connection
        self._s3 = ReportS3Client()

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        task_context = _task_context(task.get("task_context"))
        raw_document_id = int(task_context["raw_document_id"])

        row = await self._connection.fetchrow(
            """
            SELECT rd.stock_id,
                   rrd.s3_key,
                   rrd.parsing_status
            FROM raw_documents rd
            JOIN report_raw_details rrd ON rrd.raw_document_id = rd.id
            WHERE rd.id = $1
            """,
            raw_document_id,
        )

        if row is None:
            return {"status": "not_found", "raw_document_id": raw_document_id}
        if row["parsing_status"] != "success" or not row["s3_key"]:
            # 아직 파싱 전이거나 S3에 PDF가 없음 → 임베딩 불가
            return {"status": "not_ready", "raw_document_id": raw_document_id}

        pdf_bytes = self._s3.download_pdf(row["s3_key"])
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
        }


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
        analysis_agent: Any = None,
    ) -> None:
        self._connection = connection
        self._analysis_repository = AnalysisRepository(connection)
        self._agent = analysis_agent or ReportAnalysisAgent(
            retriever=ReportRagRetriever(connection),
            llm_client=llm_client,
            llm_model=llm_model,
        )

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        stock_code = str(task_context.get("stock_code") or "")
        run_key = str(task_context.get("run_key") or "REPORT").strip() or "REPORT"
        analysis_date = _report_analysis_date(task_context)

        quant = await self._build_quant(stock_id)

        result = await self._agent.analyze(
            SourceAgentInput(
                source="REPORT",
                stock_code=stock_code,
                stock_id=stock_id,
                analysis_date=analysis_date,
                run_key=run_key,
                context={"report_quant": quant},
            )
        )

        analysis_result = await self._analysis_repository.upsert_analysis_result(
            stock_id=stock_id,
            analysis_date=analysis_date,
            run_key=run_key,
            source_signal_event_ids=[],  # report는 signal_events 경로가 없음
            base_score=result.score,
            analysis_mode="report_rag",
            warning="; ".join(result.risk_flags) or None,
            version=result.prompt_ver,
        )
        agent_result = await self._analysis_repository.upsert_agent_result(
            result_id=analysis_result["id"],
            stock_id=stock_id,
            debate_method="D-1",
            source_signal_event_ids=[],
            method_score=result.score,
            method_signal=result.direction,
            method_detail={
                **result.method_detail,
                "summary": result.summary,
                "risk_flags": result.risk_flags,
                "needs_review": result.needs_review,
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
        return {
            "analysis_result_id": analysis_result["id"],
            "agent_result_id": agent_result["id"],
            "direction": result.direction,
            "score": result.score,
            "needs_review": result.needs_review,
            "analysis_source": result.analysis_source,
            "report_quant": quant,
        }

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


def _report_analysis_date(task_context: dict[str, Any]) -> date:
    value = task_context.get("analysis_date")
    if not value:
        return date.today()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


async def _save_to_db(
    connection: Any,
    reports: list[dict],
    stock_id: int,
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

        async with connection.transaction():
            row = await connection.fetchrow(
                """
                INSERT INTO raw_documents (
                    stock_id, source_type, source_name,
                    external_id, source_hash,
                    title, source_url, published_at,
                    collector_ver, collected_at
                )
                VALUES ($1, 'REPORT', $2, $3, $4, $5, $6, $7, $8, NOW())
                ON CONFLICT (source_hash) DO NOTHING
                RETURNING id
                """,
                stock_id,
                report.get("firm", ""),
                source_hash,          # external_id: source_hash로 통일 (VARCHAR 200 이내)
                source_hash,
                report.get("title", ""),
                report.get("pdf_direct_url") or report.get("pdf_url"),
                publish_date,
                "1.0",
            )

            if row is None:
                # 이미 존재하는 문서 → id만 조회
                row = await connection.fetchrow(
                    "SELECT id FROM raw_documents WHERE source_hash = $1",
                    source_hash,
                )

            raw_document_id = row["id"]

            await connection.execute(
                """
                INSERT INTO report_raw_details (
                    raw_document_id, stock_id,
                    securities_firm, publish_date,
                    pdf_url, parsing_status, extra_payload
                )
                VALUES ($1, $2, $3, $4, $5, 'pending', $6::jsonb)
                ON CONFLICT (raw_document_id) DO NOTHING
                """,
                raw_document_id,
                stock_id,
                report.get("firm", ""),
                publish_date,
                report.get("pdf_direct_url") or report.get("pdf_url"),
                json.dumps({"report_type": report.get("report_type")}),
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


def _extra_payload(value: Any) -> dict[str, Any]:
    """report_raw_details.extra_payload(JSONB)를 dict로 정규화.

    asyncpg는 JSONB 코덱을 등록하지 않으면 문자열로 돌려준다. str/dict/None 모두 수용.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value)
