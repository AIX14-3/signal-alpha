"""매매 부검 narrate 라인 — 부검 구조화 결과(라운드트립·3분류·계획대비실제·관측신호) → 중립 복기 서술.

부검 계산·판정은 main-server(app/postmortem)가 온디맨드로 수행하고, 이 내레이터는 그 **구조화 결과만**
입력받아 사후확신 없는 한국어 복기 서술을 만든다. LLM 은 서술만, 수치·판정은 불변. reject_advice 로
투자 권유/사후확신 비난 표현을 차단(parse_narrative 내부).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.narrate.base import (
    NarrateError,
    SourceNarrative,
    build_prompt,
    parse_narrative,
)

PROMPT_VERSION = "postmortem-narrate-v1"
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "postmortem_narrate_v1.md"


class PostmortemNarrator:
    source = "POSTMORTEM"

    def __init__(self, *, client: Any, model: str, timeout_seconds: float = 30.0) -> None:
        self._client = client
        self.model = model
        self.prompt_version = PROMPT_VERSION
        self._timeout_seconds = timeout_seconds

    async def narrate(self, *, payload: dict[str, Any]) -> SourceNarrative:
        """payload = 부검 구조화 결과(scope='trade'|'patterns'). 근거가 없으면 NarrateError."""
        if not _has_content(payload):
            raise NarrateError("POSTMORTEM narrate: no analyzable content")
        prompt = build_prompt(_PROMPT_PATH, payload)
        response_text = await self._client.complete(
            prompt=prompt, model=self.model, timeout_seconds=self._timeout_seconds
        )
        return parse_narrative(response_text, model=self.model)


def _has_content(payload: dict[str, Any]) -> bool:
    if payload.get("round_trips"):
        return True
    patterns = payload.get("patterns")
    return bool(patterns and patterns.get("sample"))
