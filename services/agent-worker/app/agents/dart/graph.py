from __future__ import annotations

from dataclasses import replace
from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.agents.dart.agent import DartAnalysisAgent
from app.analyzers.dart.llm import DartLlmAnalyzer


DART_ANALYSIS_GRAPH_NAME = "dart_analysis_v1"
DART_GRAPH_PROMPT_VERSION = "dart-graph-v1"


class DartAnalysisGraphState(TypedDict, total=False):
    input: SourceAgentInput
    output: SourceAgentOutput
    graph_nodes: list[str]


class DartAnalysisGraphAgent:
    source = "DART"

    def __init__(
        self,
        *,
        llm_analyzer: DartLlmAnalyzer | None = None,
        llm_high_impact_only: bool = True,
        analysis_agent: DartAnalysisAgent | None = None,
    ) -> None:
        self._analysis_agent = analysis_agent or DartAnalysisAgent(
            llm_analyzer=llm_analyzer,
            llm_high_impact_only=llm_high_impact_only,
        )
        self._graph = self._build_graph()

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        state = await self._graph.ainvoke(
            {
                "input": input_data,
                "graph_nodes": [],
            }
        )
        return state["output"]

    def _build_graph(self):
        graph = StateGraph(DartAnalysisGraphState)
        graph.add_node("validate_input", self._validate_input)
        graph.add_node("analyze", self._analyze)
        graph.add_node("validate_output", self._validate_output)
        graph.set_entry_point("validate_input")
        graph.add_conditional_edges(
            "validate_input",
            self._route_after_validation,
            {
                "analyze": "analyze",
                "validate_output": "validate_output",
            },
        )
        graph.add_edge("analyze", "validate_output")
        graph.add_edge("validate_output", END)
        return graph.compile()

    async def _validate_input(self, state: DartAnalysisGraphState) -> DartAnalysisGraphState:
        input_data = state["input"]
        graph_nodes = [*state.get("graph_nodes", []), "validate_input"]
        risk_flags: list[str] = []
        if input_data.source != "DART":
            risk_flags.append("source_must_be_dart")
        if not input_data.stock_code.strip():
            risk_flags.append("stock_code_required")
        if not input_data.events:
            risk_flags.append("events_required")
        if not risk_flags:
            return {**state, "graph_nodes": graph_nodes}
        return {
            **state,
            "graph_nodes": graph_nodes,
            "output": SourceAgentOutput(
                source="DART",
                stock_code=input_data.stock_code,
                direction="neutral",
                score=0,
                summary="DART analysis input did not pass graph validation.",
                risk_flags=risk_flags,
                method_detail={"validation_errors": risk_flags},
                needs_review=True,
                data_status="failed",
                analysis_source="graph_validation",
                prompt_ver=DART_GRAPH_PROMPT_VERSION,
            ),
        }

    async def _analyze(self, state: DartAnalysisGraphState) -> DartAnalysisGraphState:
        result = await self._analysis_agent.analyze(state["input"])
        return {
            **state,
            "graph_nodes": [*state.get("graph_nodes", []), "analyze"],
            "output": result,
        }

    async def _validate_output(self, state: DartAnalysisGraphState) -> DartAnalysisGraphState:
        output = state["output"]
        graph_nodes = [*state.get("graph_nodes", []), "validate_output"]
        method_detail = {
            **output.method_detail,
            "graph": DART_ANALYSIS_GRAPH_NAME,
            "graph_nodes": graph_nodes,
        }
        return {
            **state,
            "graph_nodes": graph_nodes,
            "output": replace(output, method_detail=method_detail),
        }

    def _route_after_validation(
        self,
        state: DartAnalysisGraphState,
    ) -> Literal["analyze", "validate_output"]:
        if state.get("output") is not None:
            return "validate_output"
        return "analyze"
