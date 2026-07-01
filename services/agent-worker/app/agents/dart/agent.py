"""DART 분석 에이전트 — Phase 0(#546) 피처 전용 + Wave 2 LLM 근거(옵션).

DART 는 고정숫자 verdict 를 내지 않는다. ``build_dart_analysis_result`` 가 산출한 서술/수치 피처를
``direction="unknown"`` + ``data_status="no_signal"`` 로 감싸 반환 → AGGREGATE 점수/방향에서 자연
제외, 판정(숫자)은 Wave 3 결정론 융합(메타러너)이 소유(불변식).

Wave 2: 옵션 ``evidence_extractor``(``DartLlmEvidenceExtractor``)가 주입되면 고임팩트 공시에 한해
LLM 이 **근거**(summary/key_facts/risk_flags)를 뽑아 ``method_detail["llm_evidence"]`` 로 **additive**
하게 붙인다. LLM 은 근거만 — ``direction``/``score`` 는 불변(verdict 아님). 미주입/게이트 미통과/LLM
실패 시 규칙 피처만(결정론 폴백). ``analyzers/dart/llm.py`` 는 이 근거 경로 + 끝단 SYNTHESIZE 가
공유한다.
"""

from __future__ import annotations

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.agents.dart.evidence import DartLlmEvidenceExtractor
from app.analyzers.dart.source_result import build_dart_analysis_result
from app.schemas.evidence import SourceType

FEATURE_PROMPT_VERSION = "dart-features-v1"
# 하위호환 별칭 — 구 호출/저장 행 소비자 보호.
RULE_PROMPT_VERSION = FEATURE_PROMPT_VERSION
DartAgentResult = SourceAgentOutput


class DartAnalysisAgent:
    source: SourceType = "DART"

    def __init__(
        self,
        *,
        evidence_extractor: DartLlmEvidenceExtractor | None = None,
    ) -> None:
        # Wave 2: LLM 근거 추출기(옵션). None 이면 규칙 피처만(기본). 숫자는 어느 경우든 결정론.
        self._evidence_extractor = evidence_extractor

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        result = build_dart_analysis_result(input_data.events)
        method_detail = result.method_detail
        needs_review = result.needs_review
        llm_model: str | None = None
        llm_error: str | None = None

        if self._evidence_extractor is not None:
            evidence = await self._evidence_extractor.extract(
                input_data.events,
                stock_code=input_data.stock_code,
                rule_result=result,
            )
            if evidence.payload is not None:
                # 근거는 기존 피처 옆에 additive — 기존 키 불변(Wave 3 융합이 그대로 읽음).
                method_detail = {**method_detail, "llm_evidence": evidence.payload}
                needs_review = needs_review or evidence.needs_review
            llm_model = evidence.llm_model
            llm_error = evidence.llm_error

        return SourceAgentOutput(
            source="DART",
            stock_code=input_data.stock_code,
            direction=result.direction,  # "unknown" — 판정 없음(D1). LLM 은 근거만.
            score=result.score,  # 0.0 — 숫자는 결정론(Wave 3 융합) 소유.
            summary=result.summary,
            risk_flags=result.risk_flags,
            method_detail=method_detail,  # data_status="no_signal" (+ 옵션 llm_evidence)
            needs_review=needs_review,
            data_status="no_signal",
            analysis_source="features",  # 숫자는 피처(결정론). llm_model 은 근거 기여 provenance 만.
            llm_model=llm_model,
            prompt_ver=FEATURE_PROMPT_VERSION,
            llm_error=llm_error,
        )
