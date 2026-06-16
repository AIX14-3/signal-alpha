"""Per-document classification for the HIRING Normalizer.

Distinct from ``rules.py`` (which scores *windowed, aggregated* indicators).
This maps a single hiring posting (one ``raw_document``) to one ``signal_event``
so the queue Normalizer can populate ``source_documents``/``signal_events``.
Pure functions, no clock, no DB.

The per-document direction is intentionally coarse — the real momentum/direction
scoring happens later in the windowed ``HiringAnalyzer``. A new posting is a
labour-demand signal, so it is classified ``positive`` by default; impact rides
on the collector's ``signal_strength`` hint when present.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

EVENT_TYPE = "hiring_posting"


@dataclass(frozen=True)
class HiringEventClassification:
    event_type: str
    signal_direction: str  # positive | neutral | negative | mixed | unknown
    impact_level: str  # high | medium | low
    needs_review: bool = False


def classify_hiring_posting(row: Mapping[str, Any]) -> HiringEventClassification:
    """Classify one hiring posting row into a signal-event shape."""
    payload = _payload(row.get("extra_payload"))
    strength = _signal_strength(payload.get("signal_strength"))

    if strength is None:
        impact_level = "low"
    elif strength >= 0.66:
        impact_level = "high"
    elif strength >= 0.33:
        impact_level = "medium"
    else:
        impact_level = "low"

    # A posting that exists is hiring demand → positive. job_count is 1-per-row
    # from the collector, so it never signals contraction here.
    direction = "positive" if _job_count(row.get("job_count")) > 0 else "neutral"

    return HiringEventClassification(
        event_type=EVENT_TYPE,
        signal_direction=direction,
        impact_level=impact_level,
    )


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _signal_strength(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _job_count(value: Any) -> int:
    try:
        return int(value) if value is not None else 1
    except (TypeError, ValueError):
        return 1
