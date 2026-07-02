"""Hiring Tier-B source agent — deterministic score, gated LLM focus rationale.

Mirrors the DataLab cause agent (``app/agents/datalab/agent.py``): the rule
analyzer owns score & direction, and a **gate** decides whether to spend one LLM
call turning the postings' skill/duty mix into a human "what is this company
hiring for" rationale. The LLM only writes *evidence* — it never moves the score.

Invariants (docs §9/§10, [[hiring-ml-rejected]]):
  - The rule ``HiringAnalyzer`` owns score & direction. Phase 0 → ``unknown`` /
    ``0.0``; the agent passes them through byte-for-byte on every path.
  - Focus gate: no skill/duty content (or no classifier) → skip the LLM call.
  - LLM failure / missing key degrades to the deterministic focus summary
    (``analysis_source="rules_fallback"``, ``llm_error`` recorded).
  - No buy/sell, no confidence, no target price — the focus is a descriptive tag.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.agents.hiring.llm_classifier import PROMPT_VERSION, HiringSkillClassifier
from app.agents.requery_focus import focus_hint_from_context
from app.analyzers.config import HiringRuleConfig
from app.analyzers.hiring import HiringAnalyzer
from app.analyzers.hiring.indicators import compute_indicators
from app.analyzers.hiring.job_functions import LABELS, classify_job_function
from app.schemas.source_result import EvidenceItem, SourceResult

logger = logging.getLogger(__name__)

# How many skills / functions to surface in the focus evidence.
_TOP_FUNCTIONS = 3
_TOP_SKILLS = 5


@dataclass(frozen=True)
class HiringFocus:
    """Deterministic skill/duty focus extracted from the postings (LLM-free)."""

    top_functions: list[str] = field(default_factory=list)  # Korean labels
    top_skills: list[str] = field(default_factory=list)
    momentum_pct: float | None = None

    @property
    def is_empty(self) -> bool:
        return not self.top_functions and not self.top_skills

    def summary_text(self) -> str:
        bits: list[str] = []
        if self.top_functions:
            bits.append(f"주요 채용 직무: {', '.join(self.top_functions)}")
        if self.top_skills:
            bits.append(f"요구 기술: {', '.join(self.top_skills)}")
        return " · ".join(bits)


class HiringAnalysisAgent:
    source = "HIRING"

    def __init__(
        self,
        *,
        analyzer: HiringAnalyzer | None = None,
        classifier: HiringSkillClassifier | None = None,
        config: HiringRuleConfig | None = None,
    ) -> None:
        self._config = config or HiringRuleConfig.from_env()
        self._analyzer = analyzer or HiringAnalyzer(self._config)
        self._classifier = classifier

    # -- composed entrypoint ------------------------------------------------ #
    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        rule = await self.run_rules(input_data)
        focus = self._build_focus(input_data)
        if not self.should_dig(rule, focus):
            return self.build_rules_output(rule)
        return await self.classify_focus(input_data, rule, focus)

    # -- discrete steps ----------------------------------------------------- #
    async def run_rules(self, input_data: SourceAgentInput) -> SourceResult:
        return await self._analyzer.analyze(input_data.stock_code, input_data.evidence)

    def should_dig(self, rule: SourceResult, focus: HiringFocus) -> bool:
        """Focus gate: only spend an LLM call when there is skill/duty content to
        interpret and the rule run did not fail."""
        if self._classifier is None or rule.data_status == "failed":
            return False
        return not focus.is_empty

    async def classify_focus(
        self,
        input_data: SourceAgentInput,
        rule: SourceResult,
        focus: HiringFocus,
    ) -> SourceAgentOutput:
        # Defensive: normally gated by should_dig, but guard here too so a direct
        # call (or -O stripping asserts) degrades cleanly instead of crashing.
        if self._classifier is None:
            return self.build_rules_output(rule)
        try:
            verdict = await self._classifier.classify(
                stock_code=rule.stock_code,
                top_functions=focus.top_functions,
                top_skills=focus.top_skills,
                momentum_pct=focus.momentum_pct,
                # Orchestrator re-query hint (Wave-3): narrows the focus rationale
                # re-read to the flagged axis. None on a plain analyze → prompt
                # byte-identical. (Distinct from HiringFocus above, which is the
                # agent's own skill/duty extraction, not the orchestrator's ask.)
                requery_focus=focus_hint_from_context(input_data.context),
            )
            rationale = verdict.rationale or focus.summary_text()
            return self._focus_output(
                rule,
                focus=focus,
                rationale=rationale,
                focus_label=verdict.focus,
                focus_source="llm",
                llm_model=self._classifier.model,
            )
        except Exception as exc:  # noqa: BLE001 — degrade to deterministic focus
            logger.warning(
                "Hiring focus LLM 분류 실패 (stock=%s): %s — 결정론 포커스 요약으로 폴백",
                rule.stock_code,
                exc,
            )
            return self._focus_output(
                rule,
                focus=focus,
                rationale=focus.summary_text(),
                focus_label=None,
                focus_source="rules_fallback",
                llm_model=None,
                llm_error=str(exc),
            )

    # -- output builders ---------------------------------------------------- #
    def build_rules_output(self, rule: SourceResult) -> SourceAgentOutput:
        return SourceAgentOutput(
            source="HIRING",
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

    def _focus_output(
        self,
        rule: SourceResult,
        *,
        focus: HiringFocus,
        rationale: str,
        focus_label: str | None,
        focus_source: str,
        llm_model: str | None,
        llm_error: str | None = None,
    ) -> SourceAgentOutput:
        detail = _base_detail(rule)
        # Surface the focus through evidence_items (round-trips via _from_output) so
        # it survives into the persisted signal, not just the transient method_detail.
        evidence_items = list(detail.get("evidence_items") or [])
        evidence_items.append(
            asdict(
                EvidenceItem(
                    # Distinct from the rule analyzer's descriptive "채용 직무·기술
                    # 포커스" item — this is the agent's interpretive focus.
                    title="채용 전략 포커스",
                    summary=rationale,
                    published_at=rule.evidence_items[0].published_at if rule.evidence_items else None,
                    source_name="HIRING",
                )
            )
        )
        detail["evidence_items"] = evidence_items
        # Structured focus for direct/FE consumers. Note: the alternative pipeline's
        # _from_output only round-trips evidence_items/summary into the persisted
        # SourceResult, so this key is transient there — the durable channel is the
        # focus evidence item + summary above.
        detail["hiring_focus"] = {
            "label": focus_label,
            "top_functions": focus.top_functions,
            "top_skills": focus.top_skills,
            "source": focus_source,
        }
        return SourceAgentOutput(
            source="HIRING",
            stock_code=rule.stock_code,
            direction=rule.direction,  # unchanged — focus never moves direction
            score=rule.score,  # unchanged — focus never moves the score
            summary=_summary_with_focus(rule.summary, focus_label, rationale),
            risk_flags=list(rule.risk_flags),
            method_detail=detail,
            needs_review=rule.data_status in ("partial", "failed"),
            data_status=rule.data_status,
            analysis_source="llm" if focus_source == "llm" else "rules_fallback",
            llm_model=llm_model or rule.llm_model,
            prompt_ver=PROMPT_VERSION,
            llm_error=llm_error,
        )

    # -- deterministic focus extraction ------------------------------------- #
    def _build_focus(self, input_data: SourceAgentInput) -> HiringFocus:
        rows = _extract_rows(input_data.evidence)
        if not rows:
            return HiringFocus()
        as_of = _as_of(input_data)
        metadata = input_data.evidence[0].metadata if input_data.evidence else {}
        indicators = compute_indicators(
            rows,
            as_of=as_of,
            lookback_days=self._config.lookback_days,
            sector_demand=metadata.get("sector_demand"),
        )
        function_counter: Counter[str] = Counter()
        for row in rows:
            key = classify_job_function(row.get("job_title"))
            if key is not None:
                function_counter[LABELS.get(key, key)] += 1
        top_functions = [label for label, _ in function_counter.most_common(_TOP_FUNCTIONS)]
        return HiringFocus(
            top_functions=top_functions,
            top_skills=list(indicators.top_skills[:_TOP_SKILLS]),
            momentum_pct=indicators.momentum_pct,
        )


def _base_detail(rule: SourceResult) -> dict[str, Any]:
    """method_detail the orchestrator's ``_from_output`` round-trips back to a
    SourceResult (evidence_items rebuilt; data_status carried)."""
    detail: dict[str, Any] = {"data_status": rule.data_status}
    if rule.evidence_items:
        detail["evidence_items"] = [asdict(item) for item in rule.evidence_items]
    return detail


def _summary_with_focus(summary: str, focus_label: str | None, rationale: str) -> str:
    tag = f"[채용 포커스: {focus_label}] " if focus_label else "[채용 포커스] "
    return f"{summary} {tag}{rationale}".rstrip()


def _extract_rows(evidence: list) -> list[dict]:
    for item in evidence:
        rows = item.metadata.get("rows")
        if rows:
            return rows
    return []


def _as_of(input_data: SourceAgentInput) -> date:
    if input_data.analysis_date is not None:
        return input_data.analysis_date
    metadata = input_data.evidence[0].metadata if input_data.evidence else {}
    raw = metadata.get("as_of")
    if isinstance(raw, date):
        return raw
    if raw:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            pass
    return date.today()
