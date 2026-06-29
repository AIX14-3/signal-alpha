"""DART narrate 라인 — 최근 DART 공시 파싱데이터 + src_dart 예측률 → 읽기 쉬운 한국어 서술.

LLM 은 서술(summary/key_facts)만 산출한다. 방향/점수 수치는 예측률(src_dart)을 그대로 인용만 한다.
공시 페이로드는 dart/llm.py 의 ``_event_payload``(재무지표·하이라이트·본문 발췌 포함)를 재사용한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.analyzers.dart.llm import (
    _event_payload,
    _loads_json_object,
)
from app.narrate.base import NarrateError, SourceNarrative

# narration 전용 투자권유 필터. 명백한 권유(매수/매도/목표가/추천, buy/sell/hold/target price)만 막고,
# 공시 서술에 자연히 등장하는 서술어("보유/소유/취득/처분")는 허용한다(원 DART 분석 필터의 "보유"
# 오탐 회피 — 자사주 보유·소유상황보고서 등). 법적 안전(투자 권유 금지)은 유지.
_ADVICE_REGEXES = (
    re.compile(r"\btarget\s+price\b", re.IGNORECASE),
    # 권유 의도 구문만 차단(매수/매도 '추천·의견·권유·하세요'). 바 단어 '매수/매도'(자사주 매수,
    # 기관 매수세, '매수 우위' 방향표시 등 사실 서술)는 허용.
    re.compile(r"매[수도]\s*(추천|의견|권유|하세요|하십시오|를\s*추천|를\s*권유)"),
    re.compile(r"매[수도]\s*(하시기|하는\s*것이\s*좋)"),
)
_ADVICE_TERMS = ("목표가", "투자 추천", "투자추천", "추천합니다", "추천드립니다", "사세요", "파세요", "매수의견", "매도의견")


def _reject_advice(values: list[str]) -> None:
    text = " ".join(values)
    for rx in _ADVICE_REGEXES:
        if rx.search(text):
            raise NarrateError("narrate response contained investment advice language")
    for term in _ADVICE_TERMS:
        if term in text:
            raise NarrateError(f"narrate response contained advice term: {term}")

PROMPT_VERSION = "dart-narrate-v1"
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "dart_narrate_v1.md"
_MAX_EVENTS = 12
_MAX_KEY_FACTS = 6


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
        prompt = _build_prompt(
            stock_code=stock_code, events=events, prediction_rate=prediction_rate
        )
        response_text = await self._client.complete(
            prompt=prompt, model=self.model, timeout_seconds=self._timeout_seconds
        )
        return _parse(response_text, model=self.model)


def select_narrate_events(
    events: list[dict[str, Any]], *, limit: int = _MAX_EVENTS
) -> list[dict[str, Any]]:
    """서술 입력 이벤트 선별 — 임팩트(high>medium>low) 우선, 최신순. LLM 호출 1회로 상한 limit 건."""
    rank = {"high": 0, "medium": 1, "low": 2}

    def _key(e: dict[str, Any]) -> tuple[int, str]:
        return (
            rank.get(str(e.get("impact_level") or "").strip(), 3),
            str(e.get("event_date") or ""),
        )

    # 임팩트 우선 + 최신순(날짜 내림차순). 같은 임팩트면 최신 공시 먼저.
    ordered = sorted(events, key=lambda e: (_key(e)[0], _neg_date(e)))
    return ordered[:limit]


def _neg_date(e: dict[str, Any]) -> str:
    # 문자열 날짜 내림차순 정렬용 — 최신이 앞. (ISO 날짜 가정; 빈값은 뒤로.)
    d = str(e.get("event_date") or "")
    return "0000-00-00" if not d else _invert(d)


def _invert(d: str) -> str:
    # 'YYYY-MM-DD' 를 정렬상 내림차순이 되도록 자리수 보수 변환.
    return "".join(chr(0x7E - ord(ch)) if ch.isdigit() else ch for ch in d)


def _build_prompt(
    *,
    stock_code: str,
    events: list[dict[str, Any]],
    prediction_rate: dict[str, Any] | None,
) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    payload = {
        "stock_code": stock_code,
        "prediction_rate": _compact_rate(prediction_rate),
        "events": [_event_payload(event) for event in events],
    }
    return template.replace("{{INPUT_JSON}}", json.dumps(payload, ensure_ascii=False, default=str))


def _compact_rate(rate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rate:
        return None
    return {
        "score_100": rate.get("score_100"),
        "direction": rate.get("direction"),
        "confidence": rate.get("confidence"),
        "model_count": rate.get("model_count"),
    }


def _parse(response_text: str, *, model: str) -> SourceNarrative:
    try:
        payload = _loads_json_object(response_text)
    except Exception as exc:  # noqa: BLE001 — 파싱 실패는 NarrateError 로 통일
        raise NarrateError(f"DART narrate: invalid JSON ({exc})") from exc
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        raise NarrateError("DART narrate: missing summary")
    raw_facts = payload.get("key_facts") or []
    key_facts = (
        [str(x).strip() for x in raw_facts if str(x).strip()]
        if isinstance(raw_facts, list)
        else []
    )[:_MAX_KEY_FACTS]
    _reject_advice([summary, *key_facts])
    return SourceNarrative(summary=summary, key_facts=key_facts, model=model)
