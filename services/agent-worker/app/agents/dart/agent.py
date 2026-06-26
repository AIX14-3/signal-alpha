"""DART 분석 에이전트 — Phase 0(#546): 피처 전용(결정론/LLM 판정 제거).

DART 도 고정숫자 verdict 를 내지 않는다. ``build_dart_analysis_result`` 가 산출한 서술 피처를
``direction="unknown"`` + ``data_status="no_signal"`` 로 감싸 반환 → AGGREGATE 점수/방향에서 자연
제외, 판정은 학습형 메타러너 return 채널(src_dart)이 수행. LLM 판정 경로는 제거됐다(``llm_analyzer``·
``llm_high_impact_only`` 인자는 상위 호출(graph/tasks) 호환을 위해 받되 **무시**한다 —
``analyzers/dart/llm.py`` 는 dead code 로 남는다).
"""

from __future__ import annotations

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.analyzers.dart.source_result import build_dart_analysis_result

FEATURE_PROMPT_VERSION = "dart-features-v1"
# 하위호환 별칭 — 구 호출/저장 행 소비자 보호.
RULE_PROMPT_VERSION = FEATURE_PROMPT_VERSION
DartAgentResult = SourceAgentOutput


class DartAnalysisAgent:
    source = "DART"

    def __init__(
        self,
        *,
        llm_analyzer: object | None = None,  # Phase 0: 무시(graph/tasks 호출 호환용).
        llm_high_impact_only: bool = True,  # Phase 0: 무시(graph/tasks 호출 호환용).
    ) -> None:
        # Phase 0(#546): LLM 판정 경로 제거. 두 인자는 상위 호출 시그니처 호환을 위해 받되
        # 저장/사용하지 않는다(죽은 상태 방지) — analyze 는 build_dart_analysis_result 만 쓴다.
        pass

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        result = build_dart_analysis_result(input_data.events)
        return SourceAgentOutput(
            source="DART",
            stock_code=input_data.stock_code,
            direction=result.direction,  # "unknown" — 판정 없음(D1).
            score=result.score,  # 0.0
            summary=result.summary,
            risk_flags=result.risk_flags,
            method_detail=result.method_detail,  # data_status="no_signal"
            needs_review=result.needs_review,
            data_status="no_signal",
            analysis_source="features",
            llm_model=None,
            prompt_ver=FEATURE_PROMPT_VERSION,
        )
