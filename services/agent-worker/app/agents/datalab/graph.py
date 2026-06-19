"""LangGraph DataLab attention agent (협업안 §4).

Mirrors the DART graph seam (``agents/dart/graph.py``): a thin StateGraph that
drives the deterministic DataLab analyzer and, when the spike is notable, adds an
LLM *cause* tag (catalyst / fomo / price_led) inferred from search-vs-price
timing. The graph earns its keep with one real conditional — the **spike gate**:
a weak/empty signal skips cause classification entirely (no LLM cost). No retry
loop: v1 has no extra evidence to gather between iterations, so re-running the
classifier on identical input would be pure ceremony.

Invariants:
  - The rule analyzer owns score & direction. Cause is a *tag only*; it never
    changes the score (docs §9 — trace detection, not a buy/sell call).
  - LLM failure degrades to the deterministic lead/lag prelabel
    (``analysis_source="rules_fallback"``, ``llm_error`` logged) — never sinks the
    source.
  - With the classifier disabled the graph emits exactly the rule result, so
    ``DATALAB_LLM_ENABLED`` off is behaviourally identical to the rule path.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, replace
from datetime import date
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.agents.datalab.lead_lag import MIN_PRICE_POINTS, compute_lead_lag
from app.agents.datalab.llm_classifier import DataLabCauseClassifier, PROMPT_VERSION
from app.analyzers.datalab import DataLabAnalyzer
from app.schemas.source_result import SourceResult

logger = logging.getLogger(__name__)

DATALAB_GRAPH_NAME = "datalab_cause_v1"


class DataLabGraphState(TypedDict, total=False):
    input: SourceAgentInput
    rule: SourceResult
    output: SourceAgentOutput
    graph_nodes: list[str]


class DataLabAnalysisGraphAgent:
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
        # async (stock_id, as_of) -> list[{"trade_date", "close"}]; None → no price,
        # so the gate falls through to the rule result (no cause).
        self._price_provider = price_provider
        self._lookback_days = lookback_days
        self._cause_score_threshold = cause_score_threshold
        self._graph = self._build_graph()

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        state = await self._graph.ainvoke({"input": input_data, "graph_nodes": []})
        return state["output"]

    # -- graph wiring -------------------------------------------------------
    def _build_graph(self):
        graph = StateGraph(DataLabGraphState)
        graph.add_node("validate_input", self._validate_input)
        graph.add_node("analyze_rules", self._analyze_rules)
        graph.add_node("classify_cause", self._classify_cause)
        graph.add_node("validate_output", self._validate_output)
        graph.set_entry_point("validate_input")
        graph.add_conditional_edges(
            "validate_input",
            self._route_after_validation,
            {"analyze_rules": "analyze_rules", "validate_output": "validate_output"},
        )
        graph.add_conditional_edges(
            "analyze_rules",
            self._route_after_rules,  # the spike gate
            {"classify_cause": "classify_cause", "validate_output": "validate_output"},
        )
        graph.add_edge("classify_cause", "validate_output")
        graph.add_edge("validate_output", END)
        return graph.compile()

    # -- nodes --------------------------------------------------------------
    async def _validate_input(self, state: DataLabGraphState) -> DataLabGraphState:
        input_data = state["input"]
        nodes = [*state.get("graph_nodes", []), "validate_input"]
        risk_flags: list[str] = []
        if input_data.source != "DATALAB":
            risk_flags.append("source_must_be_datalab")
        if not input_data.stock_code.strip():
            risk_flags.append("stock_code_required")
        if not input_data.evidence:
            risk_flags.append("evidence_required")
        if not risk_flags:
            return {**state, "graph_nodes": nodes}
        return {
            **state,
            "graph_nodes": nodes,
            "output": SourceAgentOutput(
                source="DATALAB",
                stock_code=input_data.stock_code,
                direction="unknown",
                score=0.0,
                summary="DataLab analysis input did not pass graph validation.",
                risk_flags=risk_flags,
                method_detail={"validation_errors": risk_flags},
                needs_review=True,
                data_status="failed",
                analysis_source="graph_validation",
                prompt_ver=PROMPT_VERSION,
            ),
        }

    async def _analyze_rules(self, state: DataLabGraphState) -> DataLabGraphState:
        input_data = state["input"]
        rule = await self._analyzer.analyze(input_data.stock_code, input_data.evidence)
        return {
            **state,
            "graph_nodes": [*state.get("graph_nodes", []), "analyze_rules"],
            "rule": rule,
            "output": self._rules_output(rule),
        }

    async def _classify_cause(self, state: DataLabGraphState) -> DataLabGraphState:
        input_data = state["input"]
        rule: SourceResult = state["rule"]
        nodes = [*state.get("graph_nodes", []), "classify_cause"]

        as_of = input_data.analysis_date or date.today()
        search_rows = _search_rows(input_data)
        price_rows = await self._load_price(input_data.stock_id, as_of)
        lead_lag = compute_lead_lag(
            search_rows, price_rows, as_of=as_of, lookback_days=self._lookback_days
        )

        # No usable price series → lead/lag undecidable; emit the rule result as-is.
        if lead_lag.price_points < MIN_PRICE_POINTS or self._classifier is None:
            return {**state, "graph_nodes": nodes, "output": self._rules_output(rule)}

        try:
            verdict = await self._classifier.classify(
                stock_code=rule.stock_code,
                rule_direction=rule.direction,
                rule_score=rule.score,
                lead_lag=lead_lag,
                summary=rule.summary,
            )
            cause = verdict.cause or lead_lag.preliminary_cause
            output = self._cause_output(
                rule,
                cause=cause,
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
            output = self._cause_output(
                rule,
                cause=lead_lag.preliminary_cause,
                rationale=f"LLM 분류 실패로 규칙 예비 판정 사용 — {lead_lag.note}",
                cause_source="rules_fallback",
                llm_model=None,
                llm_error=str(exc),
            )
        return {**state, "graph_nodes": nodes, "output": output}

    async def _validate_output(self, state: DataLabGraphState) -> DataLabGraphState:
        output = state["output"]
        nodes = [*state.get("graph_nodes", []), "validate_output"]
        # Defensive score guard (rule scores are already clamped; cause never
        # touches the score, so this only catches a future wiring bug).
        score = max(-1.0, min(1.0, output.score))
        risk_flags = list(output.risk_flags)
        if score != output.score and "score_out_of_range" not in risk_flags:
            risk_flags.append("score_out_of_range")
        method_detail = {
            **output.method_detail,
            "graph": DATALAB_GRAPH_NAME,
            "graph_nodes": nodes,
        }
        return {
            **state,
            "graph_nodes": nodes,
            "output": replace(
                output, score=score, risk_flags=risk_flags, method_detail=method_detail
            ),
        }

    # -- routing ------------------------------------------------------------
    def _route_after_validation(
        self, state: DataLabGraphState
    ) -> Literal["analyze_rules", "validate_output"]:
        return "validate_output" if state.get("output") is not None else "analyze_rules"

    def _route_after_rules(
        self, state: DataLabGraphState
    ) -> Literal["classify_cause", "validate_output"]:
        """Spike gate: only spend an LLM call on a notable, non-failed signal."""
        rule: SourceResult = state["rule"]
        if self._classifier is None or rule.data_status == "failed":
            return "validate_output"
        notable = (
            "search_spike" in rule.risk_flags
            or abs(rule.score) >= self._cause_score_threshold
        )
        return "classify_cause" if notable else "validate_output"

    # -- output builders ----------------------------------------------------
    def _rules_output(self, rule: SourceResult) -> SourceAgentOutput:
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
        analysis_source = "llm" if cause_source == "llm" else "rules_fallback"
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
            analysis_source=analysis_source,
            # Carry LLM provenance forward; keep any polarity model the rule set.
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
    if cause is None:
        return summary
    return f"{summary} [원인: {cause}] {rationale}"


def _search_rows(input_data: SourceAgentInput) -> list[dict]:
    for item in input_data.evidence:
        rows = item.metadata.get("rows")
        if rows:
            return rows
    return []
