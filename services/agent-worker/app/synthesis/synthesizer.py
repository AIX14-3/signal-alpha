"""LLM 종합·설명 — 수치/판정 불변, 설명(내러티브)만 생성.

LLM 클라이언트는 기존 ``app.analyzers.dart.llm`` 의 OpenAI/Gemini 클라이언트를 재사용한다.
응답은 ``headline``/``narrative``/``key_points``/``caution_points`` JSON만 받으며, score·
direction·signal 같은 수치 필드는 **읽지 않는다**(LLM이 판정을 못 바꾸게). 모든 텍스트는
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

PROMPT_VERSION = "synthesis-v1"

# 투자조언 표현 차단 — dart/llm.py 와 동일 취지(여기서는 설명 텍스트 전체에 적용).
# 주의: "매수"/"매도" 단순 부분 문자열은 "순매도"·"매도세"·"매수세 유입" 같은 *서술적* 시장
# 표현까지 걸어 불필요한 결정론 폴백을 유발한다. 그래서 **지시(directive) 표현**(추천/권유/
# ~하세요/~하라 등)만 차단해 사실 묘사는 통과시킨다.
_PROHIBITED_ADVICE_REGEXES = [
    re.compile(r"\bbuy\b", re.IGNORECASE),
    re.compile(r"\bsell\b", re.IGNORECASE),
    re.compile(r"\bhold\b", re.IGNORECASE),
    re.compile(r"\btarget\s+price\b", re.IGNORECASE),
    re.compile(r"매[수도]\s*(?:추천|권장|권유|의견|하세요|하라|해야|하시)"),
    re.compile(r"매[수도](?:를|을)\s*권"),
]
_PROHIBITED_ADVICE_SUBSTRINGS = (
    "목표가",
    "투자 추천",
    "투자추천",
    "비중 확대",
    "비중 축소",
    "적극 매수",
    "적극 매도",
)


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
    text = " ".join(values)
    for pattern in _PROHIBITED_ADVICE_REGEXES:
        if pattern.search(text):
            raise SynthesisError("Synthesis LLM response contained investment advice language.")
    for substring in _PROHIBITED_ADVICE_SUBSTRINGS:
        if substring in text:
            raise SynthesisError("Synthesis LLM response contained investment advice language.")
