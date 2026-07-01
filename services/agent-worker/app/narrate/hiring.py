"""HIRING(채용) narrate 라인 — 채용공고 파싱 피처 + src_hiring 예측률 → 읽기 쉬운 한국어 서술.

HIRING 은 문서형 이벤트가 없는 피처형이다(analyzer 가 공고수·직전 대비 증감·주요 기술스택을 이미
``summary`` 에 담아 score_breakdown['HIRING'] 로 올린다). 그래서 PRICE narrator 와 동형으로,
score_breakdown['HIRING'] 항목(analysis)과 예측률만 입력으로 쓴다 — 새 수집/계산 없음.

LLM 은 서술(summary/key_facts)만 산출한다. 방향/점수 수치는 불변(예측률을 인용만). 채용 '사실'만
서술하고 투자 함의·전망 해석은 프롬프트에서 금지한다(방향 미확정=중립).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.narrate.base import (
    NarrateError,
    SourceNarrative,
    build_prompt,
    compact_prediction_rate,
    parse_narrative,
)

PROMPT_VERSION = "hiring-narrate-v1"
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "hiring_narrate_v1.md"


class HiringNarrator:
    source = "HIRING"

    def __init__(self, *, client: Any, model: str, timeout_seconds: float = 20.0) -> None:
        self._client = client
        self.model = model
        self.prompt_version = PROMPT_VERSION
        self._timeout_seconds = timeout_seconds

    async def narrate(
        self,
        *,
        stock_code: str,
        analysis: dict[str, Any] | None,
        prediction_rate: dict[str, Any] | None,
    ) -> SourceNarrative:
        if not analysis:
            raise NarrateError("HIRING narrate: no analysis input")
        payload = {
            "stock_code": stock_code,
            "prediction_rate": compact_prediction_rate(prediction_rate),
            "hiring_analysis": {
                "summary": (analysis or {}).get("summary"),
                "direction": (analysis or {}).get("direction"),
                "score_100": (analysis or {}).get("score_100"),
                "data_status": (analysis or {}).get("data_status"),
            },
        }
        prompt = build_prompt(_PROMPT_PATH, payload)
        response_text = await self._client.complete(
            prompt=prompt, model=self.model, timeout_seconds=self._timeout_seconds
        )
        return parse_narrative(response_text, model=self.model)
