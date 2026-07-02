"""DataLab attention agent — analysis logic (협업안 §4), langgraph-free.

Mirrors the DART split (logic here, StateGraph wiring in ``graph.py``) but exposes
its steps as discrete methods — ``run_rules`` / ``should_classify`` /
``classify_cause`` — so the graph can wire them as first-class nodes/edges (the
spike gate is a real graph branch, not hidden inside one node). ``analyze``
composes the same steps for langgraph-free use/tests.

Invariants:
  - The rule analyzer owns score & direction. Cause is a *tag only* (docs §9).
  - Spike gate: only the productized attention-spike (neutral magnitude) axis
    triggers a cause classification — a non-spiking signal skips the LLM call.
  - Non-cause output is byte-identical to the LLM-off ``RuleSourceAgent`` path
    (``_to_output``), so score/direction/risk_flags/evidence — including the
    attention layer — are unchanged; cause is only ever added *beside* them.
  - LLM failure degrades to the deterministic lead/lag prelabel
    (``analysis_source="rules_fallback"``, ``llm_error`` logged).
  - Classifier disabled → emits exactly the rule result (== LLM-off rule path).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date
from typing import Any

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.agents.datalab.lead_lag import MIN_PRICE_POINTS, LeadLag, compute_lead_lag
from app.agents.datalab.llm_classifier import DataLabCauseClassifier, PROMPT_VERSION
from app.agents.requery_focus import focus_hint_from_context
from app.agents.rule_source_agent import _to_output
from app.analyzers.datalab import DataLabAnalyzer
from app.analyzers.datalab.attention import ATTENTION_FLAG
from app.schemas.source_result import SourceResult

logger = logging.getLogger(__name__)


class DataLabAnalysisAgent:
    source = "DATALAB"

    def __init__(
        self,
        *,
        analyzer: DataLabAnalyzer | None = None,
        classifier: DataLabCauseClassifier | None = None,
        price_provider: Any | None = None,
        lookback_days: int = 30,
    ) -> None:
        self._analyzer = analyzer or DataLabAnalyzer()
        self._classifier = classifier
        # async (stock_id, as_of) -> [{"trade_date", "close"}]; None → no price.
        self._price_provider = price_provider
        self._lookback_days = lookback_days

    # -- composed entrypoint (langgraph-free) ------------------------------- #
    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        rule = await self.run_rules(input_data)
        if not self.should_classify(rule):
            return self.build_rules_output(rule)
        return await self.classify_cause(input_data, rule)

    # -- discrete steps the graph wires as nodes ---------------------------- #
    async def run_rules(self, input_data: SourceAgentInput) -> SourceResult:
        return await self._analyzer.analyze(input_data.stock_code, input_data.evidence)

    def should_classify(self, rule: SourceResult) -> bool:
        """Spike gate: ask the LLM *why* only when the neutral attention-spike
        layer already flagged a notable magnitude spike (주의/주목/급증).

        The rule analyzer emits no verdict score (feature-only since #525), so a
        score threshold would never fire — notability comes from the productized
        attention axis instead. Cause and attention stay separate axes: this only
        *triggers* on the spike flag; it never reads/moves the attention fields."""
        if self._classifier is None or rule.data_status == "failed":
            return False
        return ATTENTION_FLAG in rule.risk_flags

    async def classify_cause(
        self, input_data: SourceAgentInput, rule: SourceResult
    ) -> SourceAgentOutput:
        as_of = input_data.analysis_date or date.today()
        price_rows = await self._load_price(input_data.stock_id, as_of)
        lead_lag = compute_lead_lag(
            _search_rows(input_data), price_rows, as_of=as_of, lookback_days=self._lookback_days
        )
        # No usable price series → lead/lag undecidable; emit the rule result.
        if lead_lag.price_points < MIN_PRICE_POINTS or self._classifier is None:
            return self.build_rules_output(rule)

        try:
            verdict = await self._classifier.classify(
                stock_code=rule.stock_code,
                rule_direction=rule.direction,
                rule_score=rule.score,
                lead_lag=lead_lag,
                summary=rule.summary,
                # Orchestrator re-query hint (Wave-3): narrows the cause re-read to
                # the flagged axis. None on a plain analyze → prompt byte-identical.
                requery_focus=focus_hint_from_context(input_data.context),
            )
            return self._cause_output(
                rule,
                cause=verdict.cause or lead_lag.preliminary_cause,
                rationale=verdict.rationale,
                cause_source="llm",
                llm_model=self._classifier.model,
                lead_lag=lead_lag,
            )
        except Exception as exc:  # noqa: BLE001 — degrade to deterministic prelabel
            logger.warning(
                "DataLab cause LLM 분류 실패 (stock=%s): %s — 규칙 예비 판정으로 폴백",
                rule.stock_code,
                exc,
            )
            return self._cause_output(
                rule,
                cause=lead_lag.preliminary_cause,
                rationale=f"LLM 분류 실패로 규칙 예비 판정 사용 — {lead_lag.note}",
                cause_source="rules_fallback",
                llm_model=None,
                lead_lag=lead_lag,
                llm_error=str(exc),
            )

    # -- output builders ---------------------------------------------------- #
    def build_rules_output(self, rule: SourceResult) -> SourceAgentOutput:
        """Exact LLM-off output (== ``RuleSourceAgent``): same method_detail
        (evidence_items/report_meta), risk_flags, score/direction and the
        attention-bearing evidence — so a skipped/degraded cause run is
        indistinguishable from the current deterministic DATALAB path."""
        return _to_output(rule, PROMPT_VERSION)

    def _cause_output(
        self,
        rule: SourceResult,
        *,
        cause: Any,
        rationale: str,
        cause_source: str,
        llm_model: str | None,
        lead_lag: LeadLag | None = None,
        llm_error: str | None = None,
    ) -> SourceAgentOutput:
        # Start from the exact rule output (parity: score/direction/risk_flags/
        # evidence_items/attention untouched — cause is a tag only, docs §9), then
        # add the cause tag beside the existing method_detail keys (never mutating
        # them). ``cause_lead_lag`` is the deterministic timing the tag rests on —
        # display/audit provenance for the 근거, NOT an ML feature: it is not a
        # SourceResult field, so it does not round-trip into persistence. The ML
        # lead/lag feature is recomputed point-in-time in the Wave-3 feature path
        # (app/ml/source_features), never read from this per-run LLM artifact.
        base = _to_output(rule, PROMPT_VERSION)
        detail = dict(base.method_detail)
        if cause is not None:
            detail["cause"] = cause
            detail["cause_rationale"] = rationale
            detail["cause_source"] = cause_source
        if lead_lag is not None:
            detail["cause_lead_lag"] = {
                "search_momentum_pct": lead_lag.search_momentum_pct,
                "price_prior_return": lead_lag.price_prior_return,
                "price_recent_return": lead_lag.price_recent_return,
                "preliminary_cause": lead_lag.preliminary_cause,
            }
        return replace(
            base,
            summary=_summary_with_cause(rule.summary, cause, rationale),
            method_detail=detail,
            analysis_source="llm" if cause_source == "llm" else "rules_fallback",
            llm_model=llm_model or rule.llm_model,
            llm_error=llm_error,
        )

    async def _load_price(self, stock_id: int | None, as_of: date) -> list[dict]:
        if self._price_provider is None or stock_id is None:
            return []
        try:
            return list(await self._price_provider(stock_id, as_of))
        except Exception as exc:  # noqa: BLE001 — missing price never sinks analysis
            logger.warning("DataLab cause 가격 로드 실패 (stock_id=%s): %s", stock_id, exc)
            return []


def _summary_with_cause(summary: str, cause: Any, rationale: str) -> str:
    return summary if cause is None else f"{summary} [원인: {cause}] {rationale}"


def _search_rows(input_data: SourceAgentInput) -> list[dict]:
    for item in input_data.evidence:
        rows = item.metadata.get("rows")
        if rows:
            return rows
    return []
