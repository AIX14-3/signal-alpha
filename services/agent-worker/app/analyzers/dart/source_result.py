"""DART 공시 이벤트 → 정형 피처(서술) 산출. (#546 Phase 0 · Wave 2 피처 강화)

Phase 0(#546): 결정론 고정숫자 verdict(direction/score) 제거 — **피처 산출만**. 각 결과는
``direction="unknown"`` + ``data_status="no_signal"`` 로 반환돼 AGGREGATE 점수 평균에서 제외된다.
소스는 커버리지와 근거로만 노출된다. 이벤트 메타(event_type/direction/impact 카운트·이벤트 목록)는
서술 피처로 method_detail 에 보존되어 score_breakdown.DART 및 SYNTHESIZE 근거로 쓰인다.

Wave 2(피처 품질): method_detail 의 기존 키(source/data_status/event_count/direction_counts/
event_type_counts/impact_level_counts/events)는 **불변**이다. 정형 수치 피처는 **additive** 하게
``method_detail["derived_features"]`` 블록에만 추가한다(임팩트 가중·고임팩트 카운트·정정/리뷰 카운트·
최신 공시일 등). 숫자(score/direction)는 여전히 산출하지 않는다.
"""

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
            direction="unknown",
            score=0.0,
            summary="분석 가능한 DART 공시 이벤트가 없습니다(피처 0건).",
            risk_flags=[],
            method_detail={
                "source": "DART",
                "data_status": "no_signal",
                "event_count": 0,
                "events": [],
                "derived_features": _empty_derived_features(),
            },
            needs_review=False,
        )

    direction_counts = Counter[str]()
    event_type_counts = Counter[str]()
    impact_counts = Counter[str]()
    risk_flags: list[str] = []
    detail_events: list[dict[str, Any]] = []

    # Wave 2 additive 수치 피처 누산기 — 기존 카운트 옆에 그룹으로만 추가(기존 키 불변).
    high_impact_count = 0
    impact_weighted_sum = 0.0
    correction_count = 0
    needs_review_count = 0
    official_count = 0
    latest_event: date | None = None

    for event in events:
        direction = str(event.get("signal_direction") or "unknown")
        impact_level = str(event.get("impact_level") or "low")
        event_type = str(event.get("event_type") or "dart_disclosure")
        needs_review = bool(event.get("needs_review"))

        direction_counts[direction] += 1
        event_type_counts[event_type] += 1
        impact_counts[impact_level] += 1

        weight = _impact_weight(impact_level)
        impact_weighted_sum += weight
        if weight >= _impact_weight("medium"):
            high_impact_count += 1
        if event_type == "correction":
            correction_count += 1
        if needs_review:
            needs_review_count += 1
        if bool(event.get("is_official")):
            official_count += 1
        observed = _parse_date(event.get("event_date"))
        if observed is not None and (latest_event is None or observed > latest_event):
            latest_event = observed

        # 서술적 데이터품질 플래그(판정 아님) — 근거/리뷰 표시용.
        if needs_review:
            risk_flags.append(f"review_required:{event_type}")
        if event_type == "correction":
            risk_flags.append("correction_disclosure")

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

    unique_risk_flags = _dedupe(risk_flags)
    needs_review = bool(unique_risk_flags)

    derived_features = {
        "total_events": len(events),
        "distinct_event_types": len(event_type_counts),
        "high_impact_count": high_impact_count,
        "impact_weighted_count": round(impact_weighted_sum, 4),
        "correction_count": correction_count,
        "needs_review_count": needs_review_count,
        "official_count": official_count,
        "latest_event_date": latest_event.isoformat() if latest_event else None,
    }

    return DartAnalysisResult(
        direction="unknown",  # Phase 0: 판정 없음 — 근거/커버리지로만 노출.
        score=0.0,
        summary=(
            f"DART 공시 {len(events)}건 피처 산출"
            f"(유형 {dict(event_type_counts)}, 방향 {dict(direction_counts)}). "
            f"점수에는 반영하지 않고 근거로만 사용."
        ),
        risk_flags=unique_risk_flags,
        method_detail={
            "source": "DART",
            "data_status": "no_signal",  # AGGREGATE 점수 평균에서 제외(커버리지로만 노출).
            "event_count": len(events),
            "direction_counts": dict(direction_counts),
            "event_type_counts": dict(event_type_counts),
            "impact_level_counts": dict(impact_counts),
            "events": detail_events,
            # Wave 2: 근거/커버리지용 정형 수치 피처(additive, 기존 키 불변).
            "derived_features": derived_features,
        },
        needs_review=needs_review,
    )


# 임팩트 레벨 → 수치 가중(매그니튜드 프록시). 미지값은 0.
_IMPACT_WEIGHTS = {"high": 3.0, "medium": 2.0, "low": 1.0}


def _impact_weight(level: str) -> float:
    return _IMPACT_WEIGHTS.get(level, 0.0)


def _empty_derived_features() -> dict[str, Any]:
    return {
        "total_events": 0,
        "distinct_event_types": 0,
        "high_impact_count": 0,
        "impact_weighted_count": 0.0,
        "correction_count": 0,
        "needs_review_count": 0,
        "official_count": 0,
        "latest_event_date": None,
    }


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


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
