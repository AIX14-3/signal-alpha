"""narrate 공통 — 결과 dataclass + LLM 클라이언트 빌더(dart/llm.py 클라이언트 재사용)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class NarrateError(Exception):
    """서술 생성 실패(LLM 미구성/응답 파싱 실패 등). 호출측은 잡아서 결정론 폴백한다."""


@dataclass(frozen=True)
class SourceNarrative:
    """소스 서술 결과 — summary(읽기 쉬운 문단) + key_facts(근거 bullet). 수치는 포함하지 않는다."""

    summary: str
    key_facts: list[str] = field(default_factory=list)
    model: str | None = None


def build_narrate_client(provider: str, *, settings: Any) -> Any | None:
    """provider + settings 키로 LLM 클라이언트 생성(dart/llm.py 의 Gemini/OpenAI 클라이언트 재사용).

    엔드포인트 기본값은 클라이언트가 자체 보유 — settings 가 base_url 을 명시한 경우에만 전달한다
    (``_build_synthesizer`` 와 동일 관례). 키가 없으면 None(호출측이 폴백).
    """
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
