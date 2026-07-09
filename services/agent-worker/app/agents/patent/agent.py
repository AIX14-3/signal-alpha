"""Patent significance agent — tier-B analysis logic (기획 §2.1), langgraph-free.

Mirrors the DataLab cause agent's logic/​wiring split but is one branch simpler:
patent needs no price series or lead/lag, so there is no graph wrapper (tier B =
게이트 뒤 단발 LLM, not a multi-step ReAct loop). The steps are exposed as discrete
methods — ``run_rules`` / ``should_classify`` / ``classify_significance`` — and
``analyze`` composes them.

Invariants:
  - The rule analyzer owns score & direction. Materiality is a *tag only* (docs §9);
    the output builders always carry ``rule.score`` / ``rule.direction`` through.
  - Significance gate: a weak/empty/failed signal skips classification (no LLM call).
  - LLM failure degrades to the deterministic prelabel
    (``analysis_source="rules_fallback"``, ``llm_error`` logged).
  - Classifier disabled → emits exactly the rule result (== LLM-off rule path).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.agents.patent.llm_classifier import (
    PROMPT_VERSION,
    Materiality,
    PatentSignificanceClassifier,
)
from app.agents.requery_focus import focus_hint_from_context
from app.analyzers.patent import PatentAnalyzer
from app.schemas.source_result import SourceResult

logger = logging.getLogger(__name__)

_TOP_FILINGS = 8


class PatentSignificanceAgent:
    source = "PATENT"

    def __init__(
        self,
        *,
        analyzer: PatentAnalyzer | None = None,
        classifier: PatentSignificanceClassifier | None = None,
        gate_threshold: float = 0.2,
    ) -> None:
        self._analyzer = analyzer or PatentAnalyzer()
        self._classifier = classifier
        self._gate_threshold = gate_threshold

    # -- composed entrypoint (langgraph-free) ------------------------------- #
    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        rule = await self.run_rules(input_data)
        if not self.should_classify(rule, input_data):
            return self.build_rules_output(rule)
        return await self.classify_significance(input_data, rule)

    # -- discrete steps ----------------------------------------------------- #
    async def run_rules(self, input_data: SourceAgentInput) -> SourceResult:
        return await self._analyzer.analyze(input_data.stock_code, input_data.evidence)

    def should_classify(self, rule: SourceResult, input_data: SourceAgentInput) -> bool:
        """Significance gate: only spend an LLM call on a notable, non-failed signal.

        Notable = a rule score past the aggregation threshold OR a new tech
        category appearing in the window (a strategic-shift trace even at a modest
        score). ``unknown`` direction (no data) never classifies.
        """
        if self._classifier is None or rule.data_status == "failed":
            return False
        if rule.direction == "unknown":
            return False
        return abs(rule.score) >= self._gate_threshold or _new_category_present(input_data)

    async def classify_significance(
        self, input_data: SourceAgentInput, rule: SourceResult
    ) -> SourceAgentOutput:
        prelabel = _deterministic_prelabel(rule, input_data)
        if self._classifier is None:  # defensive — gate already guarded this
            return self.build_rules_output(rule)
        try:
            verdict = await self._classifier.classify(
                stock_code=rule.stock_code,
                rule_direction=rule.direction,
                rule_score=rule.score,
                summary=rule.summary,
                filings=_top_filings(input_data),
                prelabel=prelabel,
                # Orchestrator re-query hint (Wave-3): narrows the materiality
                # re-read to the flagged axis. None on a plain analyze → prompt
                # byte-identical.
                requery_focus=focus_hint_from_context(input_data.context),
            )
        except Exception as exc:  # noqa: BLE001 — degrade to deterministic prelabel
            logger.warning(
                "Patent significance LLM 분류 실패 (stock=%s): %s — 규칙 예비 판정으로 폴백",
                rule.stock_code,
                exc,
            )
            return self._materiality_output(
                rule,
                materiality=prelabel,
                rationale="LLM 분류 실패로 규칙 예비 판정 사용",
                materiality_source="rules_fallback",
                llm_model=None,
                llm_error=str(exc),
            )
        if verdict.materiality is None:
            # 형식 오류/enum 밖 응답 — 저장되는 값의 출처는 규칙 예비 판정이므로
            # provenance 도 rules_fallback 으로 정직하게 라벨한다("ambiguous" 는
            # 유효한 LLM 판정이라 아래 성공 경로로 그대로 통과한다).
            return self._materiality_output(
                rule,
                materiality=prelabel,
                rationale=f"LLM 응답 해석 불가 — 규칙 예비 판정 사용: {verdict.rationale}",
                materiality_source="rules_fallback",
                llm_model=None,
            )
        return self._materiality_output(
            rule,
            materiality=verdict.materiality,
            rationale=verdict.rationale,
            materiality_source="llm",
            llm_model=self._classifier.model,
        )

    # -- output builders (score/direction always the rule values) ----------- #
    def build_rules_output(self, rule: SourceResult) -> SourceAgentOutput:
        return SourceAgentOutput(
            source="PATENT",
            stock_code=rule.stock_code,
            direction=rule.direction,
            score=rule.score,
            summary=rule.summary,
            risk_flags=list(rule.risk_flags),
            method_detail=_base_detail(rule),
            needs_review=rule.data_status in ("partial", "failed"),
            data_status=rule.data_status,
            analysis_source="rules",
            llm_model=rule.llm_model,
            prompt_ver=PROMPT_VERSION,
        )

    def _materiality_output(
        self,
        rule: SourceResult,
        *,
        materiality: Materiality,
        rationale: str,
        materiality_source: str,
        llm_model: str | None,
        llm_error: str | None = None,
    ) -> SourceAgentOutput:
        detail = _base_detail(rule)
        detail["patent_materiality"] = materiality
        detail["materiality_rationale"] = rationale
        detail["materiality_source"] = materiality_source
        return SourceAgentOutput(
            source="PATENT",
            stock_code=rule.stock_code,
            direction=rule.direction,  # tag never moves direction (docs §9)
            score=rule.score,  # ...or score
            summary=_summary_with_materiality(rule.summary, materiality, rationale),
            risk_flags=list(rule.risk_flags),
            method_detail=detail,
            needs_review=rule.data_status in ("partial", "failed"),
            data_status=rule.data_status,
            analysis_source="llm" if materiality_source == "llm" else "rules_fallback",
            llm_model=llm_model or rule.llm_model,
            prompt_ver=PROMPT_VERSION,
            llm_error=llm_error,
        )


# ---------------------------------------------------------------------------
# Pure helpers (deterministic — no LLM, no clock)
# ---------------------------------------------------------------------------
def _deterministic_prelabel(rule: SourceResult, input_data: SourceAgentInput) -> Materiality:
    """Coarse materiality guess from the rule direction + new-category presence.

    The LLM confirms or corrects this; it is also the fallback tag when the LLM is
    unavailable, so it must be defined for every gated signal.
    """
    if rule.direction == "mixed" or (rule.direction == "positive" and _new_category_present(input_data)):
        return "strategic"
    if rule.direction == "positive":
        return "sustained"
    if rule.direction == "negative":
        return "routine"
    return "ambiguous"


def _rows(input_data: SourceAgentInput) -> list[dict[str, Any]]:
    for item in input_data.evidence:
        rows = item.metadata.get("rows")
        if rows:
            return list(rows)
    return []


def _new_category_present(input_data: SourceAgentInput) -> bool:
    return any(row.get("is_new_category") for row in _rows(input_data))


def _top_filings(input_data: SourceAgentInput) -> list[dict[str, Any]]:
    """Most informative filings for the prompt: new categories and high per-patent
    significance first, then most recent. Pure ordering, no LLM."""
    rows = _rows(input_data)

    def _key(row: dict[str, Any]) -> tuple:
        significance = row.get("significance")
        sig = float(significance) if significance is not None else -1.0
        return (
            1 if row.get("is_new_category") else 0,
            sig,
            str(row.get("application_date") or ""),
        )

    return sorted(rows, key=_key, reverse=True)[:_TOP_FILINGS]


def _base_detail(rule: SourceResult) -> dict[str, Any]:
    """method_detail the orchestrator's ``_from_output`` round-trips back to a
    SourceResult (evidence_items rebuilt; data_status carried)."""
    detail: dict[str, Any] = {"data_status": rule.data_status}
    if rule.evidence_items:
        detail["evidence_items"] = [asdict(item) for item in rule.evidence_items]
    # 표시 전용 특허 데이터(최근 공개 특허 + 장기 출원 추이). 이 왕복이 싣지 않으면
    # 복원된 SourceResult 에서 사라져 score_breakdown.PATENT.patent 가 영영 비어 있다.
    # 점수·방향과 무관(RuleSourceAgent._to_output 과 동일 규약).
    if rule.patent_meta is not None:
        detail["patent"] = asdict(rule.patent_meta)
    return detail


def _summary_with_materiality(summary: str, materiality: Materiality, rationale: str) -> str:
    return f"{summary} [특허 유의성: {materiality}] {rationale}"
