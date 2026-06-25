from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class DartAnalysisResult:
    direction: str
    score: float
    summary: str
    risk_flags: list[str]
    method_detail: dict[str, Any]
    needs_review: bool


def build_dart_analysis_result(events: list[dict[str, Any]]) -> DartAnalysisResult:
    if not events:
        return DartAnalysisResult(
            direction="neutral",
            score=0.0,
            summary="No DART disclosure events were available for analysis.",
            risk_flags=["no_dart_events"],
            method_detail={
                "source": "DART",
                "data_status": "no_signal",
                "event_count": 0,
                "events": [],
            },
            needs_review=False,
        )

    direction_scores = Counter[str]()
    risk_flags: list[str] = []
    detail_events: list[dict[str, Any]] = []
    score = 0.0

    for event in events:
        direction = str(event.get("signal_direction") or "unknown")
        impact_level = str(event.get("impact_level") or "low")
        event_type = str(event.get("event_type") or "dart_disclosure")
        needs_review = bool(event.get("needs_review"))

        weight = _impact_weight(impact_level)
        direction_scores[direction] += weight
        score += _score_delta(direction, impact_level)

        if needs_review:
            risk_flags.append(f"review_required:{event_type}")
        if event_type == "correction":
            risk_flags.append("correction_disclosure")
        if direction in {"mixed", "unknown"}:
            risk_flags.append(f"uncertain_direction:{event_type}")

        detail_events.append(
            {
                "id": event.get("id"),
                "event_type": event_type,
                "direction": direction,
                "impact_level": impact_level,
                "title": event.get("title"),
                "summary": event.get("summary"),
                "event_date": _date_text(event.get("event_date")),
                "evidence_url": event.get("evidence_url") or event.get("source_url"),
                "needs_review": needs_review,
            }
        )

    resolved_direction = _resolve_direction(direction_scores)
    bounded_score = round(max(-1.0, min(1.0, score)), 3)
    unique_risk_flags = _dedupe(risk_flags)
    needs_review = bool(unique_risk_flags) or resolved_direction in {"mixed", "unknown"}

    return DartAnalysisResult(
        direction=_agent_signal(resolved_direction),
        score=bounded_score,
        summary=_summary(resolved_direction, detail_events, needs_review),
        risk_flags=unique_risk_flags,
        method_detail={
            "source": "DART",
            "data_status": "partial" if needs_review else "ok",
            "event_count": len(events),
            "direction_counts": dict(direction_scores),
            "events": detail_events,
        },
        needs_review=needs_review,
    )


def _impact_weight(impact_level: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(impact_level, 1)


def _score_delta(direction: str, impact_level: str) -> float:
    weight = _impact_weight(impact_level)
    if direction == "positive":
        return 0.1 * weight
    if direction == "negative":
        return -0.1 * weight
    if direction == "mixed":
        return 0.0
    return 0.0


def _resolve_direction(direction_scores: Counter[str]) -> str:
    positive = direction_scores["positive"]
    negative = direction_scores["negative"]
    mixed = direction_scores["mixed"]

    if positive and negative:
        return "mixed"
    if mixed:
        return "mixed"
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"


def _agent_signal(direction: str) -> str:
    if direction == "unknown":
        return "neutral"
    return direction


def _summary(direction: str, events: list[dict[str, Any]], needs_review: bool) -> str:
    headline = events[0]["title"] if events else "DART disclosure"
    review_text = " Additional review is needed for correction or uncertain disclosures." if needs_review else ""
    return (
        f"DART disclosures show a {direction} information direction based on "
        f"{len(events)} official event(s). Key disclosure: {headline}.{review_text}"
    )


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
