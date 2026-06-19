"""DataLab attention agent — analysis logic (협업안 §4), langgraph-free.

Mirrors the DART split (logic here, StateGraph wiring in ``graph.py``) but exposes
its steps as discrete methods — ``run_rules`` / ``should_classify`` /
``classify_cause`` — so the graph can wire them as first-class nodes/edges (the
spike gate is a real graph branch, not hidden inside one node). ``analyze``
composes the same steps for langgraph-free use/tests.

Invariants:
  - The rule analyzer owns score & direction. Cause is a *tag only* (docs §9).
  - Spike gate: a weak/empty signal skips cause classification (no LLM call).
  - LLM failure degrades to the deterministic lead/lag prelabel
    (``analysis_source="rules_fallback"``, ``llm_error`` logged).
  - Classifier disabled → emits exactly the rule result (== LLM-off rule path).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date
from typing import Any

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.agents.datalab.lead_lag import MIN_PRICE_POINTS, compute_lead_lag
from app.agents.datalab.llm_classifier import DataLabCauseClassifier, PROMPT_VERSION
from app.analyzers.datalab import DataLabAnalyzer
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
        cause_score_threshold: float = 0.2,
    ) -> None:
        self._analyzer = analyzer or DataLabAnalyzer()
        self._classifier = classifier
        # async (stock_id, as_of) -> [{"trade_date", "close"}]; None → no price.
        self._price_provider = price_provider
        self._lookback_days = lookback_days
        self._cause_score_threshold = cause_score_threshold

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
        """Spike gate: only spend an LLM call on a notable, non-failed signal."""
        if self._classifier is None or rule.data_status == "failed":
            return False
        return "search_spike" in rule.risk_flags or abs(rule.score) >= self._cause_score_threshold

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
            )
            return self._cause_output(
                rule,
                cause=verdict.cause or lead_lag.preliminary_cause,
                rationale=verdict.rationale,
                cause_source="llm",
                llm_model=self._classifier.model,
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
                llm_error=str(exc),
            )

    # -- output builders ---------------------------------------------------- #
    def build_rules_output(self, rule: SourceResult) -> SourceAgentOutput:
        return SourceAgentOutput(
            source="DATALAB",
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

    def _cause_output(
        self,
        rule: SourceResult,
        *,
        cause: Any,
        rationale: str,
        cause_source: str,
        llm_model: str | None,
        llm_error: str | None = None,
    ) -> SourceAgentOutput:
        detail = _base_detail(rule)
        if cause is not None:
            detail["cause"] = cause
            detail["cause_rationale"] = rationale
            detail["cause_source"] = cause_source
        return SourceAgentOutput(
            source="DATALAB",
            stock_code=rule.stock_code,
            direction=rule.direction,
            score=rule.score,
            summary=_summary_with_cause(rule.summary, cause, rationale),
            risk_flags=list(rule.risk_flags),
            method_detail=detail,
            needs_review=rule.data_status in ("partial", "failed"),
            data_status=rule.data_status,
            analysis_source="llm" if cause_source == "llm" else "rules_fallback",
            llm_model=llm_model or rule.llm_model,
            prompt_ver=PROMPT_VERSION,
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


def _base_detail(rule: SourceResult) -> dict[str, Any]:
    """method_detail the orchestrator's ``_from_output`` round-trips back to a
    SourceResult (evidence_items rebuilt; data_status carried)."""
    detail: dict[str, Any] = {"data_status": rule.data_status}
    if rule.evidence_items:
        detail["evidence_items"] = [asdict(item) for item in rule.evidence_items]
    return detail


def _summary_with_cause(summary: str, cause: Any, rationale: str) -> str:
    return summary if cause is None else f"{summary} [원인: {cause}] {rationale}"


def _search_rows(input_data: SourceAgentInput) -> list[dict]:
    for item in input_data.evidence:
        rows = item.metadata.get("rows")
        if rows:
            return rows
    return []
