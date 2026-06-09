from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class DartClassification:
    event_type: str
    signal_direction: str
    impact_level: str
    needs_review: bool = False


def classify_dart_report(report_name: str, *, is_correction: bool = False) -> DartClassification:
    normalized_name = report_name.strip()

    if is_correction or _has_correction_marker(normalized_name):
        return DartClassification(
            event_type="correction",
            signal_direction="neutral",
            impact_level="low",
            needs_review=True,
        )
    if _contains_any(normalized_name, ("주요사항보고서", "二쇱슂?ы빆蹂닿퀬")):
        return DartClassification(
            event_type="material_event",
            signal_direction="mixed",
            impact_level="high",
        )
    if _contains_any(normalized_name, ("임원", "?꾩썝")) and _contains_any(normalized_name, ("주요주주", "二쇱슂二쇱＜")):
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
            "?ъ뾽蹂닿퀬",
            "諛섍린蹂닿퀬",
            "遺꾧린蹂닿퀬",
        ),
    ):
        return DartClassification(
            event_type="periodic_report",
            signal_direction="neutral",
            impact_level="medium",
        )
    if _contains_any(normalized_name, ("기업지배구조보고서", "湲곗뾽吏諛곌뎄議곕낫怨좎꽌")):
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
            "湲곗옱?뺤젙",
            "?뺤젙",
        ),
    )


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)
