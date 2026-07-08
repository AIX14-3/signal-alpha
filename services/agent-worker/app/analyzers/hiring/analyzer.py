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
                data_status="no_signal",
            )

        metadata = evidence[0].metadata
        as_of = _as_of(metadata)
        indicators = compute_indicators(
            rows,
            as_of=as_of,
            lookback_days=self._config.lookback_days,
            sector_demand=metadata.get("sector_demand"),
        )
        # 메타러너 폐기(2026-07) 후 결정론 verdict 복원 — #525 "피처 전용"은 학습형 메타러너가
        # 판정을 대신한다는 전제였으나 그 채널이 폐기됐다. 이제 채용도 특허·DART·리포트와 동일하게
        # rules.evaluate_indicators 로 방향/점수를 내고 AGGREGATE 등가중 통합 점수에 산입된다.
        assessment = evaluate_indicators(indicators, self._config)

        risk_flags = list(assessment.risk_flags)
        data_status = "ok"
        if assessment.direction == "unknown":
            data_status = "no_signal"
        elif {"insufficient_history", "stale_data", "low_base"} & set(risk_flags):
            data_status = "partial"

        latest = indicators.latest_observed_date or as_of.isoformat()
        momentum = indicators.momentum_pct
        count = metadata.get("posting_count") or indicators.observations or 0
        head = (
            f"최근 {self._config.lookback_days}일 채용공고 {count}건"
            + (f"(직전 대비 {momentum * 100:+.0f}%)" if momentum is not None else "")
        )

        # Descriptive surfacing — 기술스택만. job_title 은 노이즈가 많고, keyword 선두 [..]도
        # 소스마다 사업부/회사명/고용형태(계약직·경력)/스케줄이 섞여(실측) 신뢰할 수 있는
        # '사업부'가 아니라서 서술에서 제외한다.
        top_techs = _hiring_focus(rows)
        focus_bits: list[str] = []
        if top_techs:
            focus_bits.append(f"주요 기술: {'·'.join(top_techs)}")

        summary = (
            head
            + (", " + " · ".join(focus_bits) if focus_bits else "")
            + f" → 방향 {assessment.direction}, 점수 {assessment.score:+.3f}."
        )

        evidence_items: list[EvidenceItem] = [
            EvidenceItem(
                title=highlight,
                summary=f"{latest} 기준 채용 지표 산출 결과",
                published_at=latest,
                source_name="HIRING",
            )
            for highlight in assessment.highlights
        ]
        if focus_bits:
            evidence_items.append(
                EvidenceItem(
                    title="채용 기술 포커스",
                    summary=" · ".join(focus_bits),
                    published_at=latest,
                    source_name="HIRING",
                )
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


def _hiring_focus(rows: list[dict]) -> list[str]:
    """Top tech stacks across the postings (descriptive only — never affects the
    score). Returns [] when rows carry no ``tech_stack``.

    직무명(job_title)은 스케줄 표기·'모집' 같은 노이즈가 많아 서술에서 제외한다 — 사업부(keyword
    선두 [..], 로더가 metadata.business_units 로 제공)와 기술스택만 노출한다.
    """
    tech_counter: Counter[str] = Counter()
    for row in rows:
        techs = row.get("tech_stack") or []
        if isinstance(techs, str):
            techs = [t.strip() for t in techs.split(",")]
        tech_counter.update(t for t in techs if t)
    return [t for t, _ in tech_counter.most_common(3)]


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
