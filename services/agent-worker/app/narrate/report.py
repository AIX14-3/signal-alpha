"""REPORT(증권사 리포트) narrate 라인 — 밸류에이션 정형 피처 + src_report 예측률 → 한국어 서술.

증권사 리포트의 목표주가/투자의견/방법론(report_valuation) + 예측률을 묶어 서술한다.
LLM 은 서술만, 수치 불변. 목표주가는 '증권사 제시값'으로만 인용(서비스의 권유 아님).
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

PROMPT_VERSION = "report-narrate-v1"
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "report_narrate_v1.md"


class ReportNarrator:
    source = "REPORT"

    def __init__(self, *, client: Any, model: str, timeout_seconds: float = 20.0) -> None:
        self._client = client
        self.model = model
        self.prompt_version = PROMPT_VERSION
        self._timeout_seconds = timeout_seconds

    async def narrate(
        self,
        *,
        stock_code: str,
        valuation: dict[str, Any] | None,
        prediction_rate: dict[str, Any] | None,
        events: list[dict[str, Any]] | None = None,
    ) -> SourceNarrative:
        if not valuation and not events:
            raise NarrateError("REPORT narrate: no valuation/events input")
        payload = {
            "stock_code": stock_code,
            "prediction_rate": compact_prediction_rate(prediction_rate),
            "valuation": valuation,
            "events": [
                {
                    "title": e.get("title"),
                    "summary": e.get("summary"),
                    "event_date": e.get("event_date"),
                    "evidence_url": e.get("evidence_url"),
                }
                for e in (events or [])
            ],
        }
        prompt = build_prompt(_PROMPT_PATH, payload)
        response_text = await self._client.complete(
            prompt=prompt, model=self.model, timeout_seconds=self._timeout_seconds
        )
        return parse_narrative(response_text, model=self.model)
