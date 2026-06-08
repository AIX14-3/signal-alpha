from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class DartClassification:
    event_type: str
    signal_direction: str
    impact_level: str
    needs_review: bool = False


def classify_dart_report(report_name: str) -> DartClassification:
    normalized_name = report_name.strip()

    if "기재정정" in normalized_name or "정정" in normalized_name:
        return DartClassification(
            event_type="correction",
            signal_direction="neutral",
            impact_level="low",
            needs_review=True,
        )
    if "주요사항보고서" in normalized_name:
        return DartClassification(
            event_type="material_event",
            signal_direction="mixed",
            impact_level="high",
        )
    if "임원" in normalized_name and "주요주주" in normalized_name:
        return DartClassification(
            event_type="insider_ownership",
            signal_direction="neutral",
            impact_level="low",
        )
    if "사업보고서" in normalized_name or "반기보고서" in normalized_name or "분기보고서" in normalized_name:
        return DartClassification(
            event_type="periodic_report",
            signal_direction="neutral",
            impact_level="medium",
        )
    if "기업지배구조보고서" in normalized_name:
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
