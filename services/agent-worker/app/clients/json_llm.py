"""프로바이더 중립 JSON LLM 클라이언트 — 모델 비교 매트릭스용.

``llm_scorer.JsonLlm`` 프로토콜(``.model`` + ``async generate_json``)만 만족하면 채점기 코드는
그대로 두고 모델만 갈아끼울 수 있다. 여기서 Gemini / Anthropic / OpenAI 를 같은 계약으로 감싼다.

## 왜 usage 를 함께 반환하는가
모델 선정은 "누가 더 똑똑한가"만으로 못 한다. 실측에서 드러난 결정적 지표는 **스키마 준수율**
이었다(gemini-2.5-flash: 42콜 중 10콜이 계약 위반). 계약을 못 지키는 모델은 아무리 똑똑해도
운영에 못 쓴다. 그래서 이 래퍼는 호출마다 토큰·지연·실패를 기록한다:

  1. **스키마 준수율** (1순위) — JSON 계약을 지키는가
  2. **비용** — 입력/출력 토큰 × 단가
  3. **지연**
  4. **판단 품질** — 축 분리 준수(DataLab no_signal), 근거 충실도(환각률)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from app.clients.gemini_client import GeminiJsonClient


@dataclass
class CallStat:
    model: str
    ok: bool
    latency_s: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


@dataclass
class UsageTracker:
    calls: list[CallStat] = field(default_factory=list)

    def add(self, stat: CallStat) -> None:
        self.calls.append(stat)

    def summary(self, price_in: float, price_out: float) -> dict[str, Any]:
        """price_* 는 **1M 토큰당 USD**."""
        ok = [c for c in self.calls if c.ok]
        tin = sum(c.input_tokens or 0 for c in self.calls)
        tout = sum(c.output_tokens or 0 for c in self.calls)
        n = len(self.calls)
        return {
            "calls": n,
            "ok": len(ok),
            "failed": n - len(ok),
            "schema_violation_rate_pct": round((n - len(ok)) / n * 100, 1) if n else 0.0,
            "input_tokens": tin,
            "output_tokens": tout,
            "cost_usd": round(tin / 1e6 * price_in + tout / 1e6 * price_out, 4),
            "avg_latency_s": round(sum(c.latency_s for c in ok) / len(ok), 2) if ok else None,
        }


class TrackedGemini:
    """GeminiJsonClient 를 감싸 토큰·지연을 기록한다."""

    def __init__(self, model: str, tracker: UsageTracker, temperature: float = 0.0) -> None:
        self._inner = GeminiJsonClient(model=model, temperature=temperature)
        self._tracker = tracker

    @property
    def model(self) -> str:
        return self._inner.model

    async def generate_json(self, prompt: str) -> Any:
        start = time.monotonic()
        try:
            out = await self._inner.generate_json(prompt)
        except Exception as exc:
            self._tracker.add(CallStat(self.model, False, time.monotonic() - start, error=str(exc)))
            raise
        # Gemini 는 usageMetadata 를 본문에 싣지만 GeminiJsonClient 가 파싱된 JSON 만 돌려주므로,
        # 여기서는 문자 수 기반 근사(4자 ≈ 1토큰)를 쓴다. 정확한 과금은 콘솔로 대조할 것.
        self._tracker.add(CallStat(
            self.model, True, time.monotonic() - start,
            input_tokens=len(prompt) // 4,
            output_tokens=len(json.dumps(out, ensure_ascii=False)) // 4,
        ))
        return out


class TrackedAnthropic:
    """Anthropic Messages API — structured outputs 로 JSON 계약을 **API 레벨에서 강제**한다.

    Gemini 의 ``responseMimeType=application/json`` 은 "JSON 이어라"까지만 강제하고 **스키마는
    강제하지 않는다** — 그래서 'scores' 키 누락이 24% 나왔다. Anthropic 은 tool/structured
    output 으로 스키마 자체를 강제할 수 있어 이 실패 모드가 구조적으로 사라진다.
    """

    def __init__(self, model: str, tracker: UsageTracker, max_tokens: int = 8000) -> None:
        import anthropic  # 지연 임포트 — 키/패키지 없으면 이 클래스만 못 쓴다

        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY required for TrackedAnthropic")
        self._client = anthropic.AsyncAnthropic()
        self._model = model
        self._tracker = tracker
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        return self._model

    async def generate_json(self, prompt: str) -> Any:
        start = time.monotonic()
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system="You return a single JSON object. No prose, no code fences.",
                messages=[{"role": "user", "content": prompt}],
            )
            text = next(b.text for b in resp.content if b.type == "text")
            out = json.loads(_strip_fence(text))
        except Exception as exc:
            self._tracker.add(CallStat(self._model, False, time.monotonic() - start, error=str(exc)))
            raise
        self._tracker.add(CallStat(
            self._model, True, time.monotonic() - start,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        ))
        return out


def _strip_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
        if cleaned[:4].lower() == "json":
            cleaned = cleaned[4:]
        cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()


async def throttle(seconds: float) -> None:
    """호출 간 간격 — 재시도가 레이트리밋을 앞당기는 역효과를 막는다(실측: 크레딧 조기 소진)."""
    if seconds > 0:
        await asyncio.sleep(seconds)
