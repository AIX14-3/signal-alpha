"""LangGraph wrapper for the DataLab cause agent (협업안 §4).

Unlike DART (whose only real branch is input-validity), DataLab has a second
genuine decision — the **spike gate** (is the spike notable enough to spend an
LLM cause classification?). So this graph is one branch deeper than DART and
exposes that decision as a first-class conditional edge rather than hiding it
inside a node. Node bodies delegate to ``DataLabAnalysisAgent`` (``agent.py``),
which holds the logic and runs without langgraph.

    validate_input ─[valid?]─▶ analyze_rules ─[spike gate: notable?]─▶ classify_cause ─▶ validate_output ─▶ END
          └────(invalid)──────────────────────┘         └──────────(weak)──────────────────────┘

Score is owned by the analyzer; cause is a tag only (docs §9).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.agents.datalab.agent import DataLabAnalysisAgent
from app.agents.datalab.llm_classifier import PROMPT_VERSION
from app.schemas.source_result import SourceResult

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
        analysis_agent: DataLabAnalysisAgent | None = None,
        **agent_kwargs: Any,
    ) -> None:
        # Accept a prebuilt agent or the agent's kwargs (analyzer/classifier/
        # price_provider/lookback_days).
        self._agent = analysis_agent or DataLabAnalysisAgent(**agent_kwargs)
        self._graph = self._build_graph()

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        state = await self._graph.ainvoke({"input": input_data, "graph_nodes": []})
        return state["output"]

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
        rule = await self._agent.run_rules(state["input"])
        return {
            **state,
            "graph_nodes": [*state.get("graph_nodes", []), "analyze_rules"],
            "rule": rule,
            "output": self._agent.build_rules_output(rule),  # provisional (used if gate skips)
        }

    async def _classify_cause(self, state: DataLabGraphState) -> DataLabGraphState:
        output = await self._agent.classify_cause(state["input"], state["rule"])
        return {**state, "graph_nodes": [*state.get("graph_nodes", []), "classify_cause"], "output": output}

    async def _validate_output(self, state: DataLabGraphState) -> DataLabGraphState:
        output = state["output"]
        nodes = [*state.get("graph_nodes", []), "validate_output"]
        score = max(-1.0, min(1.0, output.score))  # defensive; cause never moves score
        risk_flags = list(output.risk_flags)
        if score != output.score and "score_out_of_range" not in risk_flags:
            risk_flags.append("score_out_of_range")
        method_detail = {**output.method_detail, "graph": DATALAB_GRAPH_NAME, "graph_nodes": nodes}
        return {
            **state,
            "graph_nodes": nodes,
            "output": replace(output, score=score, risk_flags=risk_flags, method_detail=method_detail),
        }

    def _route_after_validation(
        self, state: DataLabGraphState
    ) -> Literal["analyze_rules", "validate_output"]:
        return "validate_output" if state.get("output") is not None else "analyze_rules"

    def _route_after_rules(
        self, state: DataLabGraphState
    ) -> Literal["classify_cause", "validate_output"]:
        return "classify_cause" if self._agent.should_classify(state["rule"]) else "validate_output"
