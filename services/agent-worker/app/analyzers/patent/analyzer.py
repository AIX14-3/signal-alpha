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
from app.schemas.source_result import EvidenceItem, PatentMeta, SourceResult

# 화면 "최근 공개된 특허" 목록에 실을 최대 건수(공개일 최신순 상위 N).
_RECENT_PUBLICATIONS_LIMIT = 20


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
                data_status="no_signal",
            )

        metadata = evidence[0].metadata
        as_of = _as_of(metadata)
        indicators = compute_indicators(
            rows,
            as_of=as_of,
            lookback_days=self._config.lookback_days,
        )
        assessment = evaluate_indicators(indicators, self._config)
        # 표시 전용 구조화 데이터(점수/방향 불변): 최근 공개 특허 목록 + 장기 출원 추이.
        patent_meta = _patent_meta(rows, metadata)

        risk_flags = list(assessment.risk_flags)
        data_status = "ok"
        if "insufficient_history" in risk_flags or "stale_data" in risk_flags:
            data_status = "partial"

        # latest = 가장 최근 이벤트(공개일 폴백 출원일). 창은 공개일 기준이라
        # "최근 공개된 특허"를 집계한다.
        latest = indicators.latest_application_date or as_of.isoformat()
        summary = (
            f"{latest} 기준 최근 {self._config.lookback_days}일 공개 특허 {indicators.total}건 분석: "
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
            patent_meta=patent_meta,
        )


def _patent_meta(rows: list[dict], metadata: dict) -> PatentMeta:
    """화면용 구조화 데이터 조립(표시 전용). rows 는 이벤트(공개일 폴백 출원일) 최신순.

    - recent_publications: 상위 N건을 표시 필드만 추려 노출(공개일 최신순).
    - filing_trend: 로더가 실은 연도별 출원 건수(창과 무관한 장기 이력).
    """
    recent = [
        {
            "application_no": row.get("application_no"),
            "title": row.get("patent_title"),
            "application_date": row.get("application_date"),
            "publication_date": row.get("publication_date"),
            "tech_category": row.get("tech_category"),
        }
        for row in rows[:_RECENT_PUBLICATIONS_LIMIT]
    ]
    trend = metadata.get("filing_trend")
    filing_trend = trend if isinstance(trend, list) else []
    return PatentMeta(recent_publications=recent, filing_trend=filing_trend)


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
