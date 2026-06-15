"""PATENT source analyzer: collected patent rows in, SourceResult out.

Reads only what the loader put in ``RawEvidence.metadata`` (rows + as_of) — no
database or external API access, matching the Analyzer protocol contract.
"""

from __future__ import annotations

from datetime import date

from app.analyzers.config import PatentRuleConfig
from app.analyzers.patent.indicators import compute_indicators
from app.analyzers.patent.rules import evaluate_indicators
from app.schemas.evidence import RawEvidence
from app.schemas.source_result import EvidenceItem, SourceResult


class PatentAnalyzer:
    source = "PATENT"

    def __init__(self, config: PatentRuleConfig | None = None) -> None:
        self._config = config or PatentRuleConfig.from_env()

    async def analyze(
        self,
        stock_code: str,
        evidence: list[RawEvidence],
    ) -> SourceResult:
        rows = _extract_rows(evidence)
        if not rows:
            return SourceResult(
                source="PATENT",
                stock_code=stock_code,
                direction="unknown",
                score=0.0,
                summary="분석할 특허 데이터가 없습니다 (patent_raw_details 미적재).",
                risk_flags=["no_data"],
                data_status="failed",
            )

        metadata = evidence[0].metadata
        as_of = _as_of(metadata)
        indicators = compute_indicators(
            rows,
            as_of=as_of,
            lookback_days=self._config.lookback_days,
        )
        assessment = evaluate_indicators(indicators, self._config)

        risk_flags = list(assessment.risk_flags)
        data_status = "ok"
        if "insufficient_history" in risk_flags or "stale_data" in risk_flags:
            data_status = "partial"

        latest = indicators.latest_application_date or as_of.isoformat()
        summary = (
            f"{latest} 기준 최근 {self._config.lookback_days}일 특허 출원 {indicators.total}건 분석: "
            f"방향 {assessment.direction}, 점수 {assessment.score:+.3f}."
        )
        return SourceResult(
            source="PATENT",
            stock_code=stock_code,
            direction=assessment.direction,
            score=assessment.score,
            summary=summary,
            evidence_items=[
                EvidenceItem(
                    title=highlight,
                    summary=f"{latest} 기준 특허 출원 지표 산출 결과",
                    published_at=latest,
                    source_name="KIPRIS",
                )
                for highlight in assessment.highlights
            ],
            risk_flags=risk_flags,
            data_status=data_status,
        )


def _extract_rows(evidence: list[RawEvidence]) -> list[dict]:
    for item in evidence:
        rows = item.metadata.get("rows")
        if rows:
            return rows
    return []


def _as_of(metadata: dict) -> date:
    raw = metadata.get("as_of")
    if isinstance(raw, date):
        return raw
    if raw:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            pass
    return date.today()
