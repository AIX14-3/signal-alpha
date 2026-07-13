"""Load REPORT target-price facts for a stock into a RawEvidence.

REPORT 는 지금까지 evidence loader 가 없던 소스다 — 기존 분석 레인은
``orchestrator/report/tasks.py`` 가 태스크 안에서 직접 채점한다. 이 로더는 LLM
코호트 채점 경로의 입력을 위해 ``report_valuation_facts`` 를 다른 로더들과 같은
``RawEvidence.metadata["rows"]`` 관습으로 정식화한 것이다(연구 러너
``scripts/cohort_llm_run.py::_report_rows`` 의 raw SQL 승격).

PIT: ``publish_date <= as_of`` 를 SQL 에서 자른다. 목표주가 이력은 종목당 수백 행
수준이라 창을 따로 두지 않는다(자기 과거 12개월 비교가 self_history 재료).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.schemas.evidence import RawEvidence


class ReportEvidenceLoader:
    source = "REPORT"

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def load(
        self,
        *,
        stock_id: int,
        stock_code: str,
        as_of: date,
    ) -> list[RawEvidence]:
        records = await self._connection.fetch(
            """
            SELECT publish_date, target_price, broker
            FROM report_valuation_facts
            WHERE stock_id = $1
              AND target_price IS NOT NULL
              AND publish_date <= $2
            ORDER BY publish_date
            """,
            stock_id,
            as_of,
        )
        rows = [
            {
                "publish_date": r["publish_date"].isoformat() if r["publish_date"] else None,
                "target_price": float(r["target_price"]) if r["target_price"] is not None else None,
                "broker": r["broker"],
            }
            for r in records
        ]
        latest = rows[-1]["publish_date"] if rows else None
        return [
            RawEvidence(
                source="REPORT",
                stock_code=stock_code,
                title=f"{stock_code} 증권사 목표주가 {len(rows)}건",
                content="",
                published_at=latest,
                metadata={
                    "rows": rows,
                    "as_of": as_of.isoformat(),
                    "count": len(rows),
                    "source_name": "NAVER_RESEARCH",
                },
            )
        ]
