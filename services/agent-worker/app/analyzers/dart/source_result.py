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

# B-lite 수식(임팩트 가중 순극성 → graded → 방향)은 백테스트 계측기로 이동(2026-07-13) —
# 이 모듈은 SourceResult 조립만 담당하고 수식은 reference_rules 에서 가져다 쓴다.
from app.backtest.reference_rules.dart_score import (
    impact_weight as _impact_weight,
)
from app.backtest.reference_rules.dart_score import (
    score_impact_weighted_polarity,
)
from app.core.korean_labels import DART_EVENT_TYPE_KO, SIGNAL_KO, counts_text


def _summary_text(
    *,
    events: list[dict[str, Any]],
    data_status: str,
    event_type_counts: Counter,
    direction_counts: Counter,
) -> str:
    """소스 카드/근거에 그대로 노출되는 문장. 사람이 읽을 한국어로만 쓴다.

    예전에는 f-string 에 ``dict(Counter)`` 를 그대로 끼워 넣어
    "방향 {'neutral': 1}" 같은 파이썬 repr 이 화면에 노출됐다.
    """
    total = len(events)
    types_text = counts_text(event_type_counts, DART_EVENT_TYPE_KO)
    if data_status == "ok":
        directions_text = counts_text(direction_counts, SIGNAL_KO)
        return f"최근 공시 {total}건을 확인했습니다({types_text}). 방향은 {directions_text}입니다."
    return (
        f"최근 공시 {total}건을 확인했습니다({types_text}). "
        "방향을 가를 만한 내용은 없어 점수에는 반영하지 않고 근거로만 씁니다."
    )


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
    # B-lite: 임팩트 가중 순극성용 — 방향(positive/negative) 이벤트만 분자에 넣는다.
    positive_weight = 0.0
    negative_weight = 0.0

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
        if direction == "positive":
            positive_weight += weight
        elif direction == "negative":
            negative_weight += weight
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

    # B-lite 결정론 점수: 임팩트 가중 순극성 = (Σ긍정_w − Σ부정_w) / Σ방향_w → graded(tanh).
    # 방향(positive/negative) 이벤트가 하나도 없으면 기존 features-only 폴백(unknown/0/no_signal).
    # 수식 본체는 app.backtest.reference_rules.dart_score(백테스트 계측기)에 있다.
    polarity = score_impact_weighted_polarity(positive_weight, negative_weight)
    score = polarity.score
    signal_direction = polarity.direction
    data_status = polarity.data_status

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

    summary = _summary_text(
        events=events,
        data_status=data_status,
        event_type_counts=event_type_counts,
        direction_counts=direction_counts,
    )
    return DartAnalysisResult(
        direction=signal_direction,  # B-lite: 행위형 공시 극성 + 내부자 shares_delta 부호 순극성.
        score=score,
        summary=summary,
        risk_flags=unique_risk_flags,
        method_detail={
            "source": "DART",
            "data_status": data_status,  # 방향 이벤트 있으면 ok(블렌드 산입), 없으면 no_signal.
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
