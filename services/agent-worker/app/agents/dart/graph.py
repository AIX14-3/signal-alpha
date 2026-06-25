"""DART analysis flow: validate input → analyze → annotate.

This was a LangGraph ``StateGraph``, but the flow is a fixed 3-step linear
pipeline with a single guard (skip ``analyze`` when input validation fails), so it
is expressed directly here — no langgraph dependency. Behaviour is byte-identical
to the former graph: the ``method_detail['graph']`` / ``graph_nodes`` provenance
tags are preserved, and ``analyze`` still delegates to ``DartAnalysisAgent`` (rules
+ optional LLM) unchanged. The class name/module path are kept so callers and
stored-row consumers don't change.
"""

from __future__ import annotations

from dataclasses import replace

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.agents.dart.agent import DartAnalysisAgent
from app.analyzers.dart.llm import DartLlmAnalyzer

DART_ANALYSIS_GRAPH_NAME = "dart_analysis_v1"
DART_GRAPH_PROMPT_VERSION = "dart-graph-v1"


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

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        nodes = ["validate_input"]
        validation_errors = self._validation_errors(input_data)
        if validation_errors:
            output = SourceAgentOutput(
                source="DART",
                stock_code=input_data.stock_code,
                direction="neutral",
                score=0,
                summary="DART analysis input did not pass graph validation.",
                risk_flags=validation_errors,
                method_detail={"validation_errors": validation_errors},
                needs_review=True,
                data_status="failed",
                analysis_source="graph_validation",
                prompt_ver=DART_GRAPH_PROMPT_VERSION,
            )
        else:
            output = await self._analysis_agent.analyze(input_data)
            nodes.append("analyze")
        nodes.append("validate_output")
        return replace(
            output,
            method_detail={
                **output.method_detail,
                "graph": DART_ANALYSIS_GRAPH_NAME,
                "graph_nodes": nodes,
            },
        )

    @staticmethod
    def _validation_errors(input_data: SourceAgentInput) -> list[str]:
        risk_flags: list[str] = []
        if input_data.source != "DART":
            risk_flags.append("source_must_be_dart")
        if not input_data.stock_code.strip():
            risk_flags.append("stock_code_required")
        if not input_data.events:
            risk_flags.append("events_required")
        return risk_flags
