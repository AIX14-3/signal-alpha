from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class DartClassification:
    event_type: str
    signal_direction: str
    impact_level: str
    needs_review: bool = False


# 행위형 공시 → 부호 방향. DART 제목은 정형 서식명이라 "행위 = 사실"(추측 아님). 대개
# ``주요사항보고서(…)`` 로 감싸여 오므로 material_event catch 보다 **먼저** 매칭한다.
# (키워드 AND, event_type, signal_direction, impact_level) — 앞에서부터 첫 매치.
# ⚠️ 미검증 극성(이벤트스터디 통설): 수익 예측 아님, "방향 정렬 표시"용. 자기주식처분·전환사채류는
#    잠정 방향(후속 본문 파싱으로 정밀화). 내부자 지분(매수/매도)은 여기 아니라 dart_ownership_change
#    이벤트의 signal_direction(=shares_delta 부호)로 들어온다.
_ACTION_POLARITY: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (("자기주식", "취득"), "treasury_buyback", "positive", "high"),
    (("자사주", "취득"), "treasury_buyback", "positive", "high"),
    (("자기주식", "소각"), "treasury_cancel", "positive", "high"),
    (("자기주식", "처분"), "treasury_disposal", "negative", "medium"),
    (("무상증자",), "bonus_issue", "positive", "medium"),
    (("유상증자",), "capital_increase", "negative", "high"),
    (("현금", "배당"), "dividend", "positive", "medium"),
    (("현물배당",), "dividend", "positive", "medium"),
    (("배당", "결정"), "dividend", "positive", "medium"),
    (("단일판매",), "supply_contract", "positive", "high"),
    (("공급계약",), "supply_contract", "positive", "high"),
    (("전환사채",), "convertible_bond", "negative", "medium"),
    (("신주인수권부사채",), "bond_with_warrant", "negative", "medium"),
    (("교환사채",), "exchangeable_bond", "negative", "medium"),
)


def classify_dart_report(report_name: str, *, is_correction: bool = False) -> DartClassification:
    normalized_name = report_name.strip()

    if is_correction or _has_correction_marker(normalized_name):
        return DartClassification(
            event_type="correction",
            signal_direction="neutral",
            impact_level="low",
            needs_review=True,
        )
    for keywords, event_type, direction, impact in _ACTION_POLARITY:
        if all(keyword in normalized_name for keyword in keywords):
            return DartClassification(
                event_type=event_type,
                signal_direction=direction,
                impact_level=impact,
            )
    if _contains_any(normalized_name, ("주요사항보고서",)):
        return DartClassification(
            event_type="material_event",
            signal_direction="mixed",
            impact_level="high",
        )
    if _contains_any(normalized_name, ("임원",)) and _contains_any(normalized_name, ("주요주주",)):
        return DartClassification(
            event_type="insider_ownership",
            signal_direction="neutral",
            impact_level="low",
        )
    if _contains_any(
        normalized_name,
        (
            "사업보고서",
            "반기보고서",
            "분기보고서",
        ),
    ):
        return DartClassification(
            event_type="periodic_report",
            signal_direction="neutral",
            impact_level="medium",
        )
    if _contains_any(normalized_name, ("기업지배구조보고서",)):
        return DartClassification(
            event_type="governance_report",
            signal_direction="neutral",
            impact_level="medium",
        )

    return DartClassification(
        event_type="dart_disclosure",
        signal_direction="unknown",
        impact_level="low",
        needs_review=True,
    )


def make_dart_event_hash(stock_code: str, receipt_no: str, report_name: str) -> str:
    stable_text = f"DART|{stock_code}|{receipt_no}|{report_name}"
    return sha256(stable_text.encode("utf-8")).hexdigest()


def _has_correction_marker(report_name: str) -> bool:
    normalized_name = report_name.lower()
    return _contains_any(
        normalized_name,
        (
            "정정",
            "correction",
            "amendment",
        ),
    )


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)
