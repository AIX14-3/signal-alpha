"""DART narrate 라인 — 최근 DART 공시 파싱데이터 + src_dart 예측률 → 읽기 쉬운 한국어 서술.

LLM 은 서술(summary/key_facts)만 산출한다. 방향/점수 수치는 예측률(src_dart)을 인용만 한다.
공시 페이로드는 dart/llm.py 의 ``_event_payload``(재무지표·하이라이트·본문 발췌 포함)를 재사용한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.analyzers.dart.llm import _event_payload
from app.narrate.base import (
    NarrateError,
    SourceNarrative,
    build_prompt,
    compact_prediction_rate,
    parse_narrative,
)

PROMPT_VERSION = "dart-narrate-v1"
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "dart_narrate_v1.md"
_MAX_EVENTS = 12
# 본문 상한 — 사업보고서 등 evidence_text 가 수 MB 인 경우 _event_payload 의 재무지표 추출/정규식
# 스캔이 무거워지므로(SYNTHESIZE 핫패스), 추출 전에 절단한다(LLM 입력은 어차피 2500자 발췌).
_MAX_EVIDENCE_CHARS = 20000


def _cap_evidence(event: dict[str, Any]) -> dict[str, Any]:
    text = event.get("evidence_text")
    if isinstance(text, str) and len(text) > _MAX_EVIDENCE_CHARS:
        return {**event, "evidence_text": text[:_MAX_EVIDENCE_CHARS]}
    return event


class DartNarrator:
    source = "DART"

    def __init__(self, *, client: Any, model: str, timeout_seconds: float = 20.0) -> None:
        self._client = client
        self.model = model
        self.prompt_version = PROMPT_VERSION
        self._timeout_seconds = timeout_seconds

    async def narrate(
        self,
        *,
        stock_code: str,
        events: list[dict[str, Any]],
        prediction_rate: dict[str, Any] | None,
    ) -> SourceNarrative:
        if not events:
            raise NarrateError("DART narrate: no events")
        payload = {
            "stock_code": stock_code,
            "prediction_rate": compact_prediction_rate(prediction_rate),
            "events": [_event_payload(_cap_evidence(event)) for event in events],
        }
        prompt = build_prompt(_PROMPT_PATH, payload)
        response_text = await self._client.complete(
            prompt=prompt, model=self.model, timeout_seconds=self._timeout_seconds
        )
        return parse_narrative(response_text, model=self.model)


def select_narrate_events(
    events: list[dict[str, Any]], *, limit: int = _MAX_EVENTS
) -> list[dict[str, Any]]:
    """서술 입력 이벤트 선별 — 임팩트(high>medium>low) 우선, 최신순. LLM 호출 1회로 상한 limit 건."""
    rank = {"high": 0, "medium": 1, "low": 2}
    ordered = sorted(
        events,
        key=lambda e: (
            rank.get(str(e.get("impact_level") or "").strip(), 3),
            _neg_date(e),
        ),
    )
    return ordered[:limit]


def _neg_date(e: dict[str, Any]) -> str:
    d = str(e.get("event_date") or "")
    return "0000-00-00" if not d else "".join(
        chr(0x7E - ord(ch)) if ch.isdigit() else ch for ch in d
    )
