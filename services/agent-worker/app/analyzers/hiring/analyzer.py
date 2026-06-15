"""HIRING source analyzer: collected job-postings rows in, SourceResult out.

Reads only ``RawEvidence.metadata`` (rows + as_of) — no database or external API
access, matching the Analyzer protocol contract.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from app.analyzers.config import HiringRuleConfig
from app.analyzers.hiring.indicators import compute_indicators
from app.analyzers.hiring.rules import evaluate_indicators
from app.schemas.evidence import RawEvidence
from app.schemas.source_result import EvidenceItem, SourceResult


class HiringAnalyzer:
    source = "HIRING"

    def __init__(self, config: HiringRuleConfig | None = None) -> None:
        self._config = config or HiringRuleConfig.from_env()

    async def analyze(
        self,
        stock_code: str,
        evidence: list[RawEvidence],
    ) -> SourceResult:
        rows = _extract_rows(evidence)
        if not rows:
            return SourceResult(
                source="HIRING",
                stock_code=stock_code,
                direction="unknown",
                score=0.0,
                summary="분석할 채용 데이터가 없습니다 (hiring_raw_details 미적재).",
                risk_flags=["no_data"],
                data_status="failed",
            )

        metadata = evidence[0].metadata
        as_of = _as_of(metadata)
        indicators = compute_indicators(
            rows,
            as_of=as_of,
            lookback_days=self._config.lookback_days,
            sector_demand=metadata.get("sector_demand"),
        )
        assessment = evaluate_indicators(indicators, self._config)

        risk_flags = list(assessment.risk_flags)
        data_status = "ok"
        if "insufficient_history" in risk_flags or "stale_data" in risk_flags:
            data_status = "partial"

        latest = indicators.latest_observed_date or as_of.isoformat()
        summary = (
            f"{latest} 기준 최근 {self._config.lookback_days}일 채용 공고 {indicators.observations}건 분석: "
            f"방향 {assessment.direction}, 점수 {assessment.score:+.3f}."
        )

        # Descriptive keyword/tech surfacing — what the hiring is FOR. Purely
        # informational (does not touch the score); empty when the loader did not
        # attach posting descriptors.
        top_techs, top_keywords = _hiring_focus(rows)
        focus_bits: list[str] = []
        if top_techs:
            focus_bits.append(f"주요 기술: {', '.join(top_techs)}")
        if top_keywords:
            focus_bits.append(f"주요 직무: {', '.join(top_keywords)}")

        evidence_items: list[EvidenceItem] = []
        if focus_bits:
            focus_desc = " · ".join(focus_bits)
            summary = f"{summary} | {focus_desc}"
            evidence_items.append(
                EvidenceItem(
                    title="채용 직무·기술 포커스",
                    summary=focus_desc,
                    published_at=latest,
                    source_name="HIRING",
                )
            )
        evidence_items.extend(
            EvidenceItem(
                title=highlight,
                summary=f"{latest} 기준 채용 공고 지표 산출 결과",
                published_at=latest,
                source_name="HIRING",
            )
            for highlight in assessment.highlights
        )

        return SourceResult(
            source="HIRING",
            stock_code=stock_code,
            direction=assessment.direction,
            score=assessment.score,
            summary=summary,
            evidence_items=evidence_items,
            risk_flags=risk_flags,
            data_status=data_status,
        )


def _extract_rows(evidence: list[RawEvidence]) -> list[dict]:
    for item in evidence:
        rows = item.metadata.get("rows")
        if rows:
            return rows
    return []


# Common job-title noise filtered out before keyword counting (ported from the
# legacy inline HiringAnalyzer so its descriptive surfacing is preserved here).
_JOB_TITLE_STOPWORDS = {
    "engineer", "researcher", "developer", "analyst",
    "개발", "연구", "엔지니어", "분석", "팀",
    "부", "팀장", "리드", "lead",
}


def _hiring_focus(rows: list[dict]) -> tuple[list[str], list[str]]:
    """Top tech stacks and job-title keywords across the postings.

    Descriptive only — never affects the score. Returns ([], []) when rows carry
    no ``tech_stack`` / ``job_title`` (e.g. loader did not attach descriptors).
    """
    tech_counter: Counter[str] = Counter()
    titles: list[str] = []
    for row in rows:
        techs = row.get("tech_stack") or []
        if isinstance(techs, str):
            techs = [t.strip() for t in techs.split(",")]
        tech_counter.update(t for t in techs if t)
        title = row.get("job_title")
        if title:
            titles.append(str(title))
    top_techs = [t for t, _ in tech_counter.most_common(3)]
    return top_techs, _title_keywords(titles, k=3)


def _title_keywords(job_titles: list[str], k: int = 3) -> list[str]:
    words: list[str] = []
    for title in job_titles:
        parts = title.replace("/", " ").replace("-", " ").split()
        words.extend(
            w.strip() for w in parts if w.strip().lower() not in _JOB_TITLE_STOPWORDS
        )
    return [w for w, _ in Counter(words).most_common(k)]


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
