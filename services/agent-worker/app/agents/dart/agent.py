from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analyzers.dart.llm import DartLlmAnalyzer, should_use_dart_llm
from app.analyzers.dart.source_result import build_dart_analysis_result


RULE_PROMPT_VERSION = "dart-rules-v1"


@dataclass(frozen=True)
class DartAgentResult:
    direction: str
    score: int
    summary: str
    risk_flags: list[str]
    method_detail: dict[str, Any]
    needs_review: bool
    analysis_source: str
    llm_model: str | None
    prompt_ver: str
    llm_error: str | None = None


class DartAnalysisAgent:
    def __init__(
        self,
        *,
        llm_analyzer: DartLlmAnalyzer | None = None,
        llm_high_impact_only: bool = True,
    ) -> None:
        self._llm_analyzer = llm_analyzer
        self._llm_high_impact_only = llm_high_impact_only

    async def analyze(self, *, stock_code: str, events: list[dict[str, Any]]) -> DartAgentResult:
        rule_result = build_dart_analysis_result(events)
        if self._llm_analyzer is None or not should_use_dart_llm(
            events,
            high_impact_only=self._llm_high_impact_only,
        ):
            return DartAgentResult(
                direction=rule_result.direction,
                score=rule_result.score,
                summary=rule_result.summary,
                risk_flags=rule_result.risk_flags,
                method_detail=rule_result.method_detail,
                needs_review=rule_result.needs_review,
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
            return DartAgentResult(
                direction=rule_result.direction,
                score=rule_result.score,
                summary=rule_result.summary,
                risk_flags=rule_result.risk_flags,
                method_detail=rule_result.method_detail,
                needs_review=rule_result.needs_review,
                analysis_source="rules_fallback",
                llm_model=None,
                prompt_ver=RULE_PROMPT_VERSION,
                llm_error=str(exc),
            )

        return DartAgentResult(
            direction=llm_result.direction,
            score=llm_result.score,
            summary=llm_result.summary,
            risk_flags=llm_result.risk_flags,
            method_detail={
                **rule_result.method_detail,
                "data_status": "partial" if llm_result.needs_review else "ok",
                "llm_confidence": llm_result.confidence,
                "key_facts": llm_result.key_facts,
            },
            needs_review=llm_result.needs_review,
            analysis_source="llm",
            llm_model=self._llm_analyzer.model,
            prompt_ver=self._llm_analyzer.prompt_version,
        )
