"""LangGraph wrapper for the DataLab cause agent (협업안 §4).

Mirrors ``agents/dart/graph.py``: a thin StateGraph that adds input validation
and observability around the analysis logic, which lives in
``DataLabAnalysisAgent`` (``agent.py``). The ``analyze`` node delegates there, so
all rules / spike-gate / lead-lag / LLM-cause / fallback behaviour is shared with
the langgraph-free agent. Keeping the wrapper thin is deliberate — the same
convention DART follows.

Score is owned by the analyzer; cause is a tag only (docs §9). A defensive score
clamp in ``validate_output`` guards against a future wiring bug.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.agents.datalab.agent import DataLabAnalysisAgent
from app.agents.datalab.llm_classifier import PROMPT_VERSION

DATALAB_GRAPH_NAME = "datalab_cause_v1"


class DataLabGraphState(TypedDict, total=False):
    input: SourceAgentInput
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
        # Accept either a prebuilt agent or the agent's kwargs (analyzer/classifier/
        # price_provider/lookback_days/cause_score_threshold) for convenience.
        self._agent = analysis_agent or DataLabAnalysisAgent(**agent_kwargs)
        self._graph = self._build_graph()

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        state = await self._graph.ainvoke({"input": input_data, "graph_nodes": []})
        return state["output"]

    def _build_graph(self):
        graph = StateGraph(DataLabGraphState)
        graph.add_node("validate_input", self._validate_input)
        graph.add_node("analyze", self._analyze)
        graph.add_node("validate_output", self._validate_output)
        graph.set_entry_point("validate_input")
        graph.add_conditional_edges(
            "validate_input",
            self._route_after_validation,
            {"analyze": "analyze", "validate_output": "validate_output"},
        )
        graph.add_edge("analyze", "validate_output")
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

    async def _analyze(self, state: DataLabGraphState) -> DataLabGraphState:
        result = await self._agent.analyze(state["input"])
        return {
            **state,
            "graph_nodes": [*state.get("graph_nodes", []), "analyze"],
            "output": result,
        }

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
    ) -> Literal["analyze", "validate_output"]:
        return "validate_output" if state.get("output") is not None else "analyze"
