"""DART analysis flow: validate input → analyze → annotate.

This was a LangGraph ``StateGraph``, but the flow is a fixed 3-step linear
pipeline with a single guard (skip ``analyze`` when input validation fails), so it
is expressed directly here — no langgraph dependency. The ``method_detail['graph']``
/ ``graph_nodes`` provenance tags are preserved, and ``analyze`` delegates to
``DartAnalysisAgent`` (Phase 0: 피처 전용, 판정 없음) unchanged. The class name/module
path are kept so callers and stored-row consumers don't change.

Validation-failure output mirrors the Phase 0 철학: ``direction="unknown"`` (판정 없음,
verdict 아님) with ``data_status="failed"`` for genuine contract violations only —
빈 events 는 실패가 아니라 ``DartAnalysisAgent`` 가 no_signal 로 우아하게 처리한다.
"""

from __future__ import annotations

from dataclasses import replace

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.agents.dart.agent import DartAnalysisAgent
from app.schemas.evidence import SourceType

DART_ANALYSIS_GRAPH_NAME = "dart_analysis_v1"
DART_GRAPH_PROMPT_VERSION = "dart-graph-v1"


class DartAnalysisGraphAgent:
    source: SourceType = "DART"

    def __init__(
        self,
        *,
        analysis_agent: DartAnalysisAgent | None = None,
    ) -> None:
        self._analysis_agent = analysis_agent or DartAnalysisAgent()

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        nodes = ["validate_input"]
        validation_errors = self._validation_errors(input_data)
        if validation_errors:
            output = SourceAgentOutput(
                source="DART",
                stock_code=input_data.stock_code,
                direction="unknown",
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
        # 진짜 계약 위반만 failed 로 승격한다. 빈 events 는 위반이 아니라 정상적인
        # no_signal 케이스 — DartAnalysisAgent(build_dart_analysis_result)가 우아하게 처리하므로
        # 여기서 events_required 로 막지 않는다(두 경로의 data_status 일치).
        risk_flags: list[str] = []
        if input_data.source != "DART":
            risk_flags.append("source_must_be_dart")
        if not input_data.stock_code.strip():
            risk_flags.append("stock_code_required")
        return risk_flags
