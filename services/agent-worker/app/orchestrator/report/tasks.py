from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from app.collectors.report.crawler import collect_stock
from app.collectors.report.pdf_downloader import download_and_upload, make_filename, make_s3_key
from app.collectors.report.parsers.run_parser import process_from_s3
from app.collectors.report.s3_client import ReportS3Client
from app.orchestrator.queue.task_types import PROCESS_REPORT
from signal_alpha_data_access.repositories import ProcessingQueueRepository


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

        date_end = datetime.now()
        date_start = date_end - timedelta(days=days_back)

        reports = collect_stock(
            stock_name="",
            stock_code=stock_code,
            max_pages=20,
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
                   rrd.report_type,
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

        filename = make_filename({
            "firm": row["securities_firm"],
            "date": str(row["publish_date"]).replace("-", "."),
            "report_type": row["report_type"] or "cr",
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
                    pdf_url, parsing_status
                )
                VALUES ($1, $2, $3, $4, $5, 'pending')
                ON CONFLICT (raw_document_id) DO NOTHING
                """,
                raw_document_id,
                stock_id,
                report.get("firm", ""),
                publish_date,
                report.get("pdf_direct_url") or report.get("pdf_url"),
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
