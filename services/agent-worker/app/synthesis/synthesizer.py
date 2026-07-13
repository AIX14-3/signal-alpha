"""LLM 종합·설명 — 수치/판정 불변, 설명(내러티브)만 생성.

숫자의 소유자는 이제 **상류 채점 단계**다(LLM 코호트 채점 SCORE_COHORT, 또는 플래그 off 시
결정론 규칙 — 2026-07-13 "숫자는 결정론 소유" 불변식 폐기). 이 서술 단계의 숫자 불변 가드는
그 폐기와 무관하게 **유지**한다: 판정 주체는 상류 하나여야 하고, 서술 LLM 이 여기서 숫자를
다시 만지면 판정이 둘이 된다(같은 신호에 두 숫자).

LLM 클라이언트는 기존 ``app.analyzers.dart.llm`` 의 OpenAI/Gemini 클라이언트를 재사용한다.
응답은 ``headline``/``narrative``/``key_points``/``caution_points`` JSON만 받으며, score·
direction·signal 같은 수치 필드는 **읽지 않는다**(서술 LLM이 판정을 못 바꾸게). 모든 텍스트는
투자조언 표현(매수/매도/목표가 등) 검증을 통과해야 한다 — 실패 시 SynthesisError.

``build_context`` 로 결정론/ML/게이트 입력을 모아 프롬프트에 주고, ``synthesize`` 가 내러티브를
반환한다. LLM 미사용/실패 시 호출측(handler)이 결정론 폴백 내러티브로 대체한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.analyzers.dart.llm import LlmClient
from app.policy_safety import find_investment_advice_in

PROMPT_VERSION = "synthesis-v1"

# 투자조언 표현 차단은 ``app.policy_safety`` 단일 소스(directive-only)로 통합됐다.


class SynthesisError(ValueError):
    pass


@dataclass(frozen=True)
class RiskNarrative:
    headline: str
    narrative: str
    key_points: list[str]
    caution_points: list[str]


class Synthesizer:
    def __init__(
        self,
        *,
        client: LlmClient,
        model: str,
        prompt_version: str = PROMPT_VERSION,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._client = client
        self.model = model
        self.prompt_version = prompt_version
        self._timeout_seconds = timeout_seconds

    async def synthesize(self, context: dict[str, Any]) -> RiskNarrative:
        prompt = _build_prompt(context)
        response_text = await self._client.complete(
            prompt=prompt,
            model=self.model,
            timeout_seconds=self._timeout_seconds,
        )
        return parse_synthesis_response(response_text)


def parse_synthesis_response(response_text: str) -> RiskNarrative:
    payload = _loads_json_object(response_text)
    headline = _required_str(payload, "headline")
    narrative = _required_str(payload, "narrative")
    key_points = _string_list(payload.get("key_points"), "key_points")
    caution_points = _string_list(payload.get("caution_points"), "caution_points")
    _reject_investment_advice([headline, narrative, *key_points, *caution_points])
    return RiskNarrative(
        headline=headline,
        narrative=narrative,
        key_points=key_points,
        caution_points=caution_points,
    )


def _build_prompt(context: dict[str, Any]) -> str:
    template = _prompt_template()
    return template.replace(
        "{{INPUT_JSON}}", json.dumps(context, ensure_ascii=False, default=str)
    )


def _prompt_template() -> str:
    path = Path(__file__).resolve().parents[1] / "prompts" / "synthesis_v1.md"
    return path.read_text(encoding="utf-8")


# --- small validators (mirror app/analyzers/dart/llm.py; kept local to avoid importing privates) ---


def _loads_json_object(response_text: str) -> dict[str, Any]:
    text = response_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SynthesisError("Synthesis LLM response was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise SynthesisError("Synthesis LLM response must be a JSON object.")
    return payload


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SynthesisError(f"Synthesis LLM response missing string field: {key}")
    return value.strip()


def _string_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SynthesisError(f"Synthesis LLM field must be a list: {key}")
    return [str(item).strip() for item in value if str(item).strip()]


def _reject_investment_advice(values: list[str]) -> None:
    hit = find_investment_advice_in(values)
    if hit is not None:
        raise SynthesisError(f"Synthesis LLM response contained investment advice language: {hit}")
