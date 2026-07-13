"""narrate 공통 — 결과 dataclass + LLM 클라이언트 빌더 + 프롬프트/파싱/권유필터 공유.

DART/PRICE/REPORT narrator 가 공유한다. LLM 은 서술(summary/key_facts)만 산출하고 수치는 불변.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.analyzers.dart.llm import _loads_json_object
from app.policy_safety import find_investment_advice_in


class NarrateError(Exception):
    """서술 생성 실패(LLM 미구성/응답 파싱 실패/권유표현 등). 호출측은 잡아서 결정론 폴백한다."""


@dataclass(frozen=True)
class SourceNarrative:
    """소스 서술 결과 — summary(읽기 쉬운 문단) + key_facts(근거 bullet). 수치는 포함하지 않는다."""

    summary: str
    key_facts: list[str] = field(default_factory=list)
    model: str | None = None


# 투자권유 필터는 ``app.policy_safety`` 단일 소스로 통합됐다(채널별 사본이 강도가 달라 지시
# 표현이 약한 채널로 새어나가던 문제 + 사실 서술을 권유로 오인해 정상 출력을 막던 문제를 함께 해결).
# 위반 시 NarrateError → 호출측이 잡아 기존 요약을 유지(발행은 계속, 위반 텍스트만 정제).
def reject_advice(values: list[str]) -> None:
    hit = find_investment_advice_in(values)
    if hit is not None:
        raise NarrateError(f"narrate response contained advice term: {hit}")


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


def narrate_client_from_env(source_key: str, *, settings: Any) -> tuple[Any, str, float] | None:
    """{SRC}_USE_LLM + {SRC}_LLM_MODEL/PROVIDER/TIMEOUT env → (client, model, timeout). 미구성이면 None.

    소스 narrate 라인의 표준 게이트(_source_narrate_enabled + build_narrate_client)를 단일 진입점으로
    합친 것. USE_LLM 이 켜져 있고 모델·provider 키가 있어야 클라이언트를 만든다(부작용 없는 게이트).
    """
    import os

    if str(os.getenv(f"{source_key}_USE_LLM") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    model = str(os.getenv(f"{source_key}_LLM_MODEL") or "")
    if not model:
        return None
    provider = (os.getenv(f"{source_key}_LLM_PROVIDER") or "gemini").strip().lower()
    client = build_narrate_client(provider, settings=settings)
    if client is None:
        return None
    timeout = float(os.getenv(f"{source_key}_LLM_TIMEOUT_SECONDS") or 30.0)
    return client, model, timeout


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
