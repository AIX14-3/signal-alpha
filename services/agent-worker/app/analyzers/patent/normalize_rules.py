"""Per-document classification for the PATENT Normalizer.

Distinct from ``rules.py`` (which scores *windowed, aggregated* indicators).
This maps a single patent application (one ``raw_document``) to one
``signal_event`` so the queue Normalizer can populate
``source_documents``/``signal_events``. Pure functions, no clock, no DB.

A filing is R&D activity → ``positive`` by default. A filing in a tech category
new to the stock is a stronger expansion signal, so it gets a distinct event
type and a higher impact level. The real momentum scoring happens later in the
windowed ``PatentAnalyzer``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

EVENT_TYPE_FILING = "patent_filing"
EVENT_TYPE_NEW_CATEGORY = "patent_new_category"


@dataclass(frozen=True)
class PatentEventClassification:
    event_type: str
    signal_direction: str  # positive | neutral | negative | mixed | unknown
    impact_level: str  # high | medium | low
    needs_review: bool = False


def classify_patent_filing(row: Mapping[str, Any]) -> PatentEventClassification:
    """Classify one patent application row into a signal-event shape."""
    if _truthy(row.get("is_new_category")):
        return PatentEventClassification(
            event_type=EVENT_TYPE_NEW_CATEGORY,
            signal_direction="positive",
            impact_level="medium",
        )
    return PatentEventClassification(
        event_type=EVENT_TYPE_FILING,
        signal_direction="positive",
        impact_level="low",
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "t"}
