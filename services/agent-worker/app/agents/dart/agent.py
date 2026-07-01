"""DART 분석 에이전트 — Phase 0(#546): 피처 전용(결정론/LLM 판정 제거).

DART 도 고정숫자 verdict 를 내지 않는다. ``build_dart_analysis_result`` 가 산출한 서술 피처를
``direction="unknown"`` + ``data_status="no_signal"`` 로 감싸 반환 → AGGREGATE 점수/방향에서 자연
제외, 판정은 학습형 메타러너 return 채널(src_dart)이 수행. LLM 판정 경로는 배선되지 않는다(Tier-C
정리: 에이전트·graph·핸들러의 ``llm_analyzer``·``llm_high_impact_only`` 호환 인자를 제거). DART 판정용
``DartLlmAnalyzer`` 는 미배선으로 남고, ``analyzers/dart/llm.py`` 의 LLM 클라이언트/파서는 끝단
SYNTHESIZE 가 재사용하므로 모듈 자체는 보존한다.
"""

from __future__ import annotations

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.analyzers.dart.source_result import build_dart_analysis_result
from app.schemas.evidence import SourceType

FEATURE_PROMPT_VERSION = "dart-features-v1"
# 하위호환 별칭 — 구 호출/저장 행 소비자 보호.
RULE_PROMPT_VERSION = FEATURE_PROMPT_VERSION
DartAgentResult = SourceAgentOutput


class DartAnalysisAgent:
    source: SourceType = "DART"

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
