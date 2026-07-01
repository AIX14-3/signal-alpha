"""DART LLM 근거(evidence) 추출기 — Wave 2.

판정(verdict)이 아니라 **근거**만 산출한다. 고임팩트 공시에 한해 LLM 이 summary/key_facts/
risk_flags 를 뽑아 ``method_detail["llm_evidence"]`` 로 additive 하게 붙인다. ``direction``/``score``
는 **채택하지 않는다** — 숫자는 결정론(Wave 3 융합)이 소유(불변식). LLM 실패/게이트 미통과 시
근거 블록을 생략하는 **결정론 폴백**. 투자조언 차단은 ``DartLlmAnalyzer`` 내부 ``parse_dart_llm_response``
(→ ``_reject_investment_advice`` + policy_safety)가 그대로 강제한다(위반=DartLlmAnalysisError→폴백).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analyzers.dart.llm import DartLlmAnalyzer, should_use_dart_llm
from app.analyzers.dart.source_result import DartAnalysisResult


@dataclass(frozen=True)
class DartEvidenceResult:
    """근거 추출 결과. payload 없으면 근거 블록 미부착(피처만)."""

    payload: dict[str, Any] | None = None
    llm_model: str | None = None
    needs_review: bool = False
    llm_error: str | None = None


class DartLlmEvidenceExtractor:
    """고임팩트 DART 공시 → LLM 근거(summary/key_facts/risk_flags). verdict 미채택."""

    def __init__(self, *, analyzer: DartLlmAnalyzer, high_impact_only: bool = True) -> None:
        self._analyzer = analyzer
        self._high_impact_only = high_impact_only

    async def extract(
        self,
        events: list[dict[str, Any]],
        *,
        stock_code: str,
        rule_result: DartAnalysisResult,
    ) -> DartEvidenceResult:
        # 게이트: 고임팩트 공시가 아니면 LLM 미호출(피처만, 비용/노이즈 절감).
        if not should_use_dart_llm(events, high_impact_only=self._high_impact_only):
            return DartEvidenceResult()
        try:
            analysis = await self._analyzer.analyze(
                events=events, rule_result=rule_result, stock_code=stock_code
            )
        except Exception as exc:  # noqa: BLE001 — LLM 실패는 결정론 폴백(파이프라인 불가침).
            return DartEvidenceResult(llm_error=f"{type(exc).__name__}: {exc}"[:300])
        # verdict(direction/score)는 채택하지 않는다 — 근거만 method_detail 에 additive.
        payload = {
            "summary": analysis.summary,
            "key_facts": analysis.key_facts,
            "risk_flags": analysis.risk_flags,
            "llm_model": self._analyzer.model,
            "prompt_ver": self._analyzer.prompt_version,
        }
        return DartEvidenceResult(
            payload=payload,
            llm_model=self._analyzer.model,
            needs_review=bool(analysis.needs_review),
        )


def build_dart_evidence_extractor(settings: Any) -> DartLlmEvidenceExtractor | None:
    """config 로 근거 추출기 생성. dart_use_llm off / 모델 미설정 / 키 없음이면 None(피처만).

    narrate 와 동일 규약: LLM 은 명시 설정(provider+model+key)일 때만 배선된다.
    """
    if not getattr(settings, "dart_use_llm", False):
        return None
    model = str(getattr(settings, "dart_llm_model", "") or "")
    if not model:
        return None

    from app.narrate.base import build_narrate_client
    from app.observability.langsmith import maybe_trace

    provider = str(getattr(settings, "dart_llm_provider", "gemini") or "gemini")
    client = build_narrate_client(provider, settings=settings)
    if client is None:
        return None
    client = maybe_trace(client, name="dart_evidence")

    analyzer = DartLlmAnalyzer(
        client=client,
        model=model,
        timeout_seconds=float(getattr(settings, "dart_llm_timeout_seconds", 20.0)),
    )
    return DartLlmEvidenceExtractor(
        analyzer=analyzer,
        high_impact_only=bool(getattr(settings, "dart_llm_high_impact_only", True)),
    )
