"""
Report Collector
report_raw DB 테이블에서 해당 종목 리포트 메타데이터를 RawEvidence로 변환한다.
DB가 비어 있을 경우 parsed_reports.json으로 fallback한다.
외부 API나 파싱을 직접 수행하지 않는다.
"""
import json

import psycopg2

from app.core.config import get_settings
from app.schemas.evidence import RawEvidence


class ReportCollector:
    source = "report"

    def collect(self, stock_code: str) -> list[RawEvidence]:
        evidence = self._collect_from_db(stock_code)
        if evidence:
            return evidence
        # TODO: report_raw 백필 완료 후 이 fallback 제거
        # (python parsers/vector_store.py --backfill-raw 실행 후)
        return self._collect_from_json(stock_code)

    def _collect_from_db(self, stock_code: str) -> list[RawEvidence]:
        try:
            settings = get_settings()
            conn = psycopg2.connect(settings.database_url)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT rrd.securities_firm,
                       rrd.publish_date,
                       rrd.report_type,
                       rd.title,
                       rd.source_url,
                       rrd.target_price,
                       rrd.investment_opinion,
                       rrd.key_rationale,
                       rrd.extracted_text
                FROM raw_documents rd
                JOIN report_raw_details rrd ON rrd.raw_document_id = rd.id
                JOIN stocks s               ON s.id = rd.stock_id
                WHERE s.ticker = %s
                  AND rrd.parsing_status = 'success'
                ORDER BY rrd.publish_date DESC
                """,
                (stock_code,),
            )
            rows = cur.fetchall()
            conn.close()
        except Exception:
            return []

        evidence = []
        for row in rows:
            firm, date, report_type, title, pdf_url, target_price, opinion, key_rationale, raw_text_preview = row
            content_parts = []
            if key_rationale:
                content_parts.append(key_rationale)
            if raw_text_preview:
                content_parts.append(raw_text_preview[:500])
            evidence.append(
                RawEvidence(
                    source="report",
                    stock_code=stock_code,
                    title=title or "",
                    content="\n".join(content_parts),
                    published_at=date,
                    url=pdf_url,
                    metadata={
                        "firm": firm or "",
                        "report_type": report_type or "",
                        "target_price": str(target_price or ""),
                        "opinion": opinion or "unknown",
                    },
                )
            )
        return evidence

    def _collect_from_json(self, stock_code: str) -> list[RawEvidence]:
        settings = get_settings()
        path = settings.parsed_reports_path
        if not path.exists():
            return []

        with open(path, encoding="utf-8") as f:
            reports: list[dict] = json.load(f)

        evidence = []
        for r in reports:
            if r.get("stock_code") != stock_code:
                continue
            if not r.get("processed"):
                continue

            content_parts = []
            if r.get("key_rationale"):
                content_parts.append(r["key_rationale"])
            if r.get("raw_text_preview"):
                content_parts.append(r["raw_text_preview"])

            evidence.append(
                RawEvidence(
                    source="report",
                    stock_code=stock_code,
                    title=r.get("title", ""),
                    content="\n".join(content_parts),
                    published_at=r.get("date"),
                    url=r.get("pdf_url"),
                    metadata={
                        "firm": r.get("firm", ""),
                        "report_type": r.get("report_type", ""),
                        "target_price": str(r.get("target_price") or ""),
                        "opinion": r.get("opinion", "unknown"),
                    },
                )
            )
        return evidence
