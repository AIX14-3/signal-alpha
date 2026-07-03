"""LLM 판정 + 코드단 검증 — LLM 은 제안, 결정은 코드.

LLM 클라이언트는 기존 GeminiJsonClient(재시도·strict JSON 내장)를 재사용하고,
이 모듈은 출력의 형식·범위·금지어를 강제한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.guard.gdelt import GuardArticle
from app.guard.prompts import GUARD_PROMPT_VERSION, build_prompt

ALLOWED_DIRECTIONS = {"escalation", "deescalation", "unclear"}

# 투자조언 혼입 가드레일 — summary/evidence 는 차단 안내 화면에 노출될 수 있는 텍스트다.
# "보유" 는 제외한다: 지정학 텍스트에 흔한 "핵보유국"·"핵무기 보유" 등을 오탐해
# 판정을 통째로 거부(→ 사이클 중단·기사 미저장·다음 사이클 재실패)하면, 정작 이
# 스위치가 필요한 위기 구간에 영구 블라인드가 된다. 영어 hold 는 아래 \b 경계로 잡는다.
_FORBIDDEN_KO = ("매수", "매도", "목표가")
_FORBIDDEN_EN = re.compile(r"\b(buy|sell|hold)\b", re.IGNORECASE)


class GuardJudgeError(RuntimeError):
    """LLM 출력이 계약(스키마·범위·금지어)을 위반했다."""


@dataclass(frozen=True)
class GeoRiskJudgment:
    severity: int
    is_geopolitical_risk: bool
    direction: str
    summary: str
    regions: list[str]
    affected_themes: list[str]
    confidence: int
    evidence: list[str]
    prompt_version: str = GUARD_PROMPT_VERSION


def validate_judgment(payload: Any) -> GeoRiskJudgment:
    if not isinstance(payload, dict):
        raise GuardJudgeError(f"judgment must be a JSON object, got {type(payload).__name__}")
    severity = _clamp_int(payload.get("severity"), field="severity")
    confidence = _clamp_int(payload.get("confidence"), field="confidence")
    is_risk = payload.get("is_geopolitical_risk")
    if not isinstance(is_risk, bool):
        raise GuardJudgeError("is_geopolitical_risk must be a boolean")
    direction = payload.get("direction")
    if direction not in ALLOWED_DIRECTIONS:
        raise GuardJudgeError(f"direction {direction!r} not in {sorted(ALLOWED_DIRECTIONS)}")
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise GuardJudgeError("summary is required")
    summary = summary.strip()
    regions = _string_list(payload.get("regions"))
    themes = _string_list(payload.get("affected_themes"))
    evidence = _string_list(payload.get("evidence"))
    for text in [summary, *evidence]:
        _reject_investment_advice(text)
    return GeoRiskJudgment(
        severity=severity,
        is_geopolitical_risk=is_risk,
        direction=direction,
        summary=summary,
        regions=regions,
        affected_themes=themes,
        confidence=confidence,
        evidence=evidence,
    )


async def judge_articles(llm: Any, articles: list[GuardArticle]) -> GeoRiskJudgment:
    """새 기사 묶음 1회 판정 — 호출 실패/계약 위반은 예외로 올려 상태 무변경을 보장."""
    prompt = build_prompt(
        [
            {
                "title": article.title,
                "url": article.url,
                "published_at": article.published_at.isoformat() if article.published_at else None,
            }
            for article in articles
        ]
    )
    payload = await llm.generate_json(prompt)
    return validate_judgment(payload)


def _clamp_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GuardJudgeError(f"{field} must be a number")
    return max(0, min(100, int(value)))


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise GuardJudgeError("list field must be an array of strings")
    return [str(item).strip() for item in value if str(item).strip()]


def _reject_investment_advice(text: str) -> None:
    lowered = text.lower()
    if any(word in text for word in _FORBIDDEN_KO) or _FORBIDDEN_EN.search(lowered):
        raise GuardJudgeError(f"investment-advice wording detected: {text[:60]!r}")
