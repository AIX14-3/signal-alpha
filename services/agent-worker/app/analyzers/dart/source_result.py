"""DART 공시 이벤트 → 정형 피처(서술) 산출. (#546 Phase 0)

Phase 0(#546): 결정론 고정숫자 verdict(direction/score) 제거 — **피처 산출만**. 방향/점수는
학습형 메타러너의 return 채널이 산출(D1). 각 결과는 ``direction="unknown"`` +
``data_status="no_signal"`` 로 반환돼 AGGREGATE 점수 평균·방향 합의에서 자연 제외된다(소스는
커버리지로만 노출, datalab/hiring Phase 0 와 동일 계약). 이벤트 메타(event_type/direction/impact
카운트·이벤트 목록)는 서술 피처로 method_detail 에 보존 — src_dart base 모델·메타러너가 DB 에서
직접 읽거나 근거로 쓴다.
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
            },
            needs_review=False,
        )

    direction_counts = Counter[str]()
    event_type_counts = Counter[str]()
    impact_counts = Counter[str]()
    risk_flags: list[str] = []
    detail_events: list[dict[str, Any]] = []

    for event in events:
        direction = str(event.get("signal_direction") or "unknown")
        impact_level = str(event.get("impact_level") or "low")
        event_type = str(event.get("event_type") or "dart_disclosure")
        needs_review = bool(event.get("needs_review"))

        direction_counts[direction] += 1
        event_type_counts[event_type] += 1
        impact_counts[impact_level] += 1

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

    return DartAnalysisResult(
        direction="unknown",  # Phase 0: 판정 없음 — 메타러너 return 채널이 산출.
        score=0.0,
        summary=(
            f"DART 공시 {len(events)}건 피처 산출"
            f"(유형 {dict(event_type_counts)}, 방향 {dict(direction_counts)}). "
            f"판정은 학습형 메타러너가 수행."
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
        },
        needs_review=needs_review,
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
