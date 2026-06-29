"""PRICE narrate 라인 — 주가·수급 분석 결과 + src_price 예측률 → 읽기 쉬운 한국어 서술.

PRICE 는 문서형 근거가 없으므로(OHLCV 파생) score_breakdown.PRICE(요약/점수/방향)와 예측률,
선택적으로 최근 가격 통계를 입력으로 쓴다. LLM 은 서술만, 수치 불변.
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

PROMPT_VERSION = "price-narrate-v1"
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "price_narrate_v1.md"


class PriceNarrator:
    source = "PRICE"

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
        price_stats: dict[str, Any] | None = None,
    ) -> SourceNarrative:
        if not analysis and not price_stats:
            raise NarrateError("PRICE narrate: no analysis input")
        payload = {
            "stock_code": stock_code,
            "prediction_rate": compact_prediction_rate(prediction_rate),
            "price_analysis": {
                "summary": (analysis or {}).get("summary"),
                "direction": (analysis or {}).get("direction"),
                "score_100": (analysis or {}).get("score_100"),
            },
            "price_stats": price_stats,
        }
        prompt = build_prompt(_PROMPT_PATH, payload)
        response_text = await self._client.complete(
            prompt=prompt, model=self.model, timeout_seconds=self._timeout_seconds
        )
        return parse_narrative(response_text, model=self.model)
