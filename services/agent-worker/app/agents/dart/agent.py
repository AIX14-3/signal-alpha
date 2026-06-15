from __future__ import annotations

from typing import cast

from app.agents.base import SourceAgentInput, SourceAgentOutput, SourceAgentStatus
from app.analyzers.dart.llm import DartLlmAnalyzer, should_use_dart_llm
from app.analyzers.dart.source_result import build_dart_analysis_result


RULE_PROMPT_VERSION = "dart-rules-v1"
DartAgentResult = SourceAgentOutput


class DartAnalysisAgent:
    source = "DART"

    def __init__(
        self,
        *,
        llm_analyzer: DartLlmAnalyzer | None = None,
        llm_high_impact_only: bool = True,
    ) -> None:
        self._llm_analyzer = llm_analyzer
        self._llm_high_impact_only = llm_high_impact_only

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        stock_code = input_data.stock_code
        events = input_data.events
        rule_result = build_dart_analysis_result(events)
        rule_data_status = str(rule_result.method_detail.get("data_status") or "ok")
        if self._llm_analyzer is None or not should_use_dart_llm(
            events,
            high_impact_only=self._llm_high_impact_only,
        ):
            return SourceAgentOutput(
                source="DART",
                stock_code=stock_code,
                direction=rule_result.direction,
                score=rule_result.score,
                summary=rule_result.summary,
                risk_flags=rule_result.risk_flags,
                method_detail=rule_result.method_detail,
                needs_review=rule_result.needs_review,
                data_status=_data_status(rule_data_status),
                analysis_source="rules",
                llm_model=None,
                prompt_ver=RULE_PROMPT_VERSION,
            )

        try:
            llm_result = await self._llm_analyzer.analyze(
                events=events,
                rule_result=rule_result,
                stock_code=stock_code,
            )
        except Exception as exc:
            return SourceAgentOutput(
                source="DART",
                stock_code=stock_code,
                direction=rule_result.direction,
                score=rule_result.score,
                summary=rule_result.summary,
                risk_flags=rule_result.risk_flags,
                method_detail=rule_result.method_detail,
                needs_review=rule_result.needs_review,
                data_status=_data_status(rule_data_status),
                analysis_source="rules_fallback",
                llm_model=None,
                prompt_ver=RULE_PROMPT_VERSION,
                llm_error=str(exc),
            )

        data_status = "partial" if llm_result.needs_review else "ok"
        return SourceAgentOutput(
            source="DART",
            stock_code=stock_code,
            direction=llm_result.direction,
            score=llm_result.score,
            summary=llm_result.summary,
            risk_flags=llm_result.risk_flags,
            method_detail={
                **rule_result.method_detail,
                "data_status": data_status,
                "llm_confidence": llm_result.confidence,
                "key_facts": llm_result.key_facts,
            },
            needs_review=llm_result.needs_review,
            data_status=data_status,
            analysis_source="llm",
            llm_model=self._llm_analyzer.model,
            prompt_ver=self._llm_analyzer.prompt_version,
        )


def _data_status(value: str) -> SourceAgentStatus:
    if value in {"ok", "partial", "failed"}:
        return cast(SourceAgentStatus, value)
    return "partial"
