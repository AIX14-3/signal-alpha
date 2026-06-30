"""Shared policy copy-safety terms for worker LLM boundaries."""

from __future__ import annotations

POLICY_RECOMMENDATION_PHRASES: tuple[str, ...] = (
    "보유 추천",
    "목표 수익률",
    "수익 예측",
    "투자 타이밍 알림",
)


def contains_policy_recommendation(text: str) -> bool:
    return any(phrase in text for phrase in POLICY_RECOMMENDATION_PHRASES)
