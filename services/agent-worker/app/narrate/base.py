"""narrate 공통 — 결과 dataclass + LLM 클라이언트 빌더 + 프롬프트/파싱/권유필터 공유.

DART/PRICE/REPORT narrator 가 공유한다. LLM 은 서술(summary/key_facts)만 산출하고 수치는 불변.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.analyzers.dart.llm import _loads_json_object
from app.policy_safety import contains_policy_recommendation


class NarrateError(Exception):
    """서술 생성 실패(LLM 미구성/응답 파싱 실패/권유표현 등). 호출측은 잡아서 결정론 폴백한다."""


@dataclass(frozen=True)
class SourceNarrative:
    """소스 서술 결과 — summary(읽기 쉬운 문단) + key_facts(근거 bullet). 수치는 포함하지 않는다."""

    summary: str
    key_facts: list[str] = field(default_factory=list)
    model: str | None = None


# narration 전용 투자권유 필터. 명백한 권유(매수/매도 추천·의견·권유·하세요, 목표가, 투자추천,
# target price)를 막고, 공시/지표 서술에 자연히 등장하는 서술어(보유/소유/취득/처분, '매수세' 등)는
# 허용한다. 법적 안전(투자 권유 금지)은 유지.
#
# H5/H6: 소스 narrator 출력은 발행물(score_breakdown.summary)에 직접 들어가므로, 끝단 synthesizer 의
# 강한 필터(synthesizer._reject_investment_advice)와 **동급**이어야 한다. 과거엔 이 필터가 더 약해
# bare buy/sell/hold·"비중 확대/축소"·"적극 매수/매도" 같은 지시 표현이 소스 서술 채널로 새어나갔다.
# 아래는 synthesizer 의 금지 셋 + 명백한 한국어 패러프레이즈(담으세요/비중을 늘리·줄이/들어가도 좋)를
# 합친 것. 위반 시 NarrateError → 호출측이 잡아 기존 요약을 유지(발행은 계속, 위반 텍스트만 정제).
_ADVICE_REGEXES = (
    re.compile(r"\bbuy\b", re.IGNORECASE),
    re.compile(r"\bsell\b", re.IGNORECASE),
    re.compile(r"\bhold\b", re.IGNORECASE),
    re.compile(r"\btarget\s+price\b", re.IGNORECASE),
    re.compile(r"매[수도]\s*(추천|의견|권유|하세요|하십시오|를\s*추천|를\s*권유)"),
    re.compile(r"매[수도]\s*(하시기|하는\s*것이\s*좋)"),
    re.compile(r"적극\s*매[수도]"),
    re.compile(r"비중\s*(을|를)?\s*(확대|축소|늘리|줄이)"),
    re.compile(r"담으(세요|십시오)"),
    re.compile(r"들어가도\s*좋"),
)
_ADVICE_TERMS = (
    "목표가", "투자 추천", "투자추천", "추천합니다", "추천드립니다", "사세요", "파세요",
    "매수의견", "매도의견", "비중 확대", "비중 축소", "적극 매수", "적극 매도",
)


def reject_advice(values: list[str]) -> None:
    text = " ".join(values)
    for rx in _ADVICE_REGEXES:
        if rx.search(text):
            raise NarrateError("narrate response contained investment advice language")
    for term in _ADVICE_TERMS:
        if term in text:
            raise NarrateError(f"narrate response contained advice term: {term}")
    if contains_policy_recommendation(text):
        raise NarrateError("narrate response contained investment advice language")


def build_prompt(template_path: Path, payload: dict[str, Any]) -> str:
    template = template_path.read_text(encoding="utf-8")
    return template.replace("{{INPUT_JSON}}", json.dumps(payload, ensure_ascii=False, default=str))


def parse_narrative(response_text: str, *, model: str, max_key_facts: int = 6) -> SourceNarrative:
    """LLM 응답(JSON {summary, key_facts}) → SourceNarrative. 권유표현이면 NarrateError."""
    try:
        payload = _loads_json_object(response_text)
    except Exception as exc:  # noqa: BLE001
        raise NarrateError(f"narrate: invalid JSON ({exc})") from exc
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        raise NarrateError("narrate: missing summary")
    raw = payload.get("key_facts") or []
    key_facts = (
        [str(x).strip() for x in raw if str(x).strip()] if isinstance(raw, list) else []
    )[:max_key_facts]
    reject_advice([summary, *key_facts])
    return SourceNarrative(summary=summary, key_facts=key_facts, model=model)


def compact_prediction_rate(rate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rate:
        return None
    return {
        "score_100": rate.get("score_100"),
        "direction": rate.get("direction"),
        "confidence": rate.get("confidence"),
        "model_count": rate.get("model_count"),
    }


def build_narrate_client(provider: str, *, settings: Any) -> Any | None:
    """provider + settings 키로 LLM 클라이언트 생성(dart/llm.py 의 Gemini/OpenAI 클라이언트 재사용)."""
    from app.analyzers.dart.llm import GeminiGenerateContentClient, OpenAiChatClient

    provider = (provider or "gemini").strip().lower()
    if provider == "gemini" and getattr(settings, "gemini_api_key", None):
        base = getattr(settings, "gemini_base_url", None)
        return GeminiGenerateContentClient(
            api_key=settings.gemini_api_key, **({"base_url": base} if base else {})
        )
    if provider == "openai" and getattr(settings, "openai_api_key", None):
        base = getattr(settings, "openai_base_url", None)
        return OpenAiChatClient(
            api_key=settings.openai_api_key, **({"base_url": base} if base else {})
        )
    return None
