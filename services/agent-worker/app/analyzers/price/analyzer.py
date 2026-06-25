"""PRICE source analyzer: collected OHLCV rows in, SourceResult out.

Reads only what the collector put in ``RawEvidence.metadata`` — no database or
external API access, matching the Analyzer protocol contract.
"""

from __future__ import annotations

from app.analyzers.price.indicators import compute_indicators
from app.analyzers.price.rules import evaluate_indicators
from app.schemas.evidence import RawEvidence
from app.schemas.source_result import EvidenceItem, SourceResult


class PriceAnalyzer:
    source = "PRICE"

    async def analyze(
        self,
        stock_code: str,
        evidence: list[RawEvidence]
    ) -> SourceResult:
        rows = _extract_rows(evidence)
        if not rows:
            return SourceResult(
                source="PRICE",
                stock_code=stock_code,
                direction="unknown",
                score=0.0,
                summary="분석할 가격 데이터가 없습니다 (ohlcv_data 미적재).",
                risk_flags=["no_data"],
                data_status="no_signal",
            )

        metadata = evidence[0].metadata
        indicators = compute_indicators(rows)
        assessment = evaluate_indicators(indicators)

        risk_flags = list(assessment.risk_flags)
        data_status = "ok"
        if metadata.get("stale"):
            risk_flags.append("stale_data")
            data_status = "partial"
        if "insufficient_history" in risk_flags:
            data_status = "partial"

        latest = metadata.get("latest_trade_date", rows[-1]["trade_date"])
        summary = (
            f"{latest} 기준 최근 {indicators.sessions}영업일 가격·수급 지표 분석: "
            f"방향 {assessment.direction}, 점수 {assessment.score:+.3f}."
        )
        return SourceResult(
            source="PRICE",
            stock_code=stock_code,
            direction=assessment.direction,
            score=assessment.score,
            summary=summary,
            evidence_items=[
                EvidenceItem(
                    title=highlight,
                    summary=f"{latest} 기준 가격·수급 지표 산출 결과",
                    published_at=latest,
                    source_name="ohlcv_data",
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
