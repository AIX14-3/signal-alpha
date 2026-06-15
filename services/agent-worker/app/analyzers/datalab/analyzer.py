"""DATALAB source analyzer: collected search-trend rows in, SourceResult out.

Reads only ``RawEvidence.metadata`` (rows + as_of) — no database or external API
access, matching the Analyzer protocol contract.
"""

from __future__ import annotations

from datetime import date

from app.analyzers.config import DataLabRuleConfig
from app.analyzers.datalab.indicators import compute_indicators
from app.analyzers.datalab.rules import evaluate_indicators
from app.schemas.evidence import RawEvidence
from app.schemas.source_result import EvidenceItem, SourceResult


class DataLabAnalyzer:
    source = "DATALAB"

    def __init__(self, config: DataLabRuleConfig | None = None) -> None:
        self._config = config or DataLabRuleConfig.from_env()

    async def analyze(
        self,
        stock_code: str,
        evidence: list[RawEvidence],
    ) -> SourceResult:
        rows = _extract_rows(evidence)
        if not rows:
            return SourceResult(
                source="DATALAB",
                stock_code=stock_code,
                direction="unknown",
                score=0.0,
                summary="분석할 검색 트렌드 데이터가 없습니다 (datalab_raw_details 미적재 또는 카테고리 미매핑).",
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

        # Provenance (Scope A): the rule score above is unchanged. We only surface
        # whether an LLM classified any of these keywords' polarity, so persistence
        # can record agent_results.llm_model and the summary can disclose it.
        llm_model, llm_count = _llm_polarity_provenance(rows)

        latest = indicators.latest_observed_date or as_of.isoformat()
        summary = (
            f"{latest} 기준 최근 {self._config.lookback_days}일 검색 트렌드 {indicators.observations}건 분석: "
            f"방향 {assessment.direction}, 점수 {assessment.score:+.3f}."
        )
        if llm_count:
            summary += f" (LLM 분류 키워드 {llm_count}건 반영)"
        return SourceResult(
            source="DATALAB",
            stock_code=stock_code,
            direction=assessment.direction,
            score=assessment.score,
            summary=summary,
            evidence_items=[
                EvidenceItem(
                    title=highlight,
                    summary=f"{latest} 기준 검색 트렌드 지표 산출 결과",
                    published_at=latest,
                    source_name="NAVER_DATALAB",
                )
                for highlight in assessment.highlights
            ],
            risk_flags=risk_flags,
            data_status=data_status,
            llm_model=llm_model,
        )


def _extract_rows(evidence: list[RawEvidence]) -> list[dict]:
    for item in evidence:
        rows = item.metadata.get("rows")
        if rows:
            return rows
    return []


def _llm_polarity_provenance(rows: list[dict]) -> tuple[str | None, int]:
    """Return (llm_model, count) for rows whose polarity was LLM-classified.

    ``llm_model`` is the first non-empty ``polarity_model`` among LLM-sourced rows,
    or None when no keyword's polarity came from the LLM lane. Pure read — it does
    not affect indicators or the score (Scope A).
    """
    llm_rows = [row for row in rows if row.get("polarity_source") == "llm"]
    model = next((row.get("polarity_model") for row in llm_rows if row.get("polarity_model")), None)
    return model, len(llm_rows)


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
