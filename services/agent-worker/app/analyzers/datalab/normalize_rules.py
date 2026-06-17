"""Per-observation classification for the DATALAB Normalizer.

Distinct from ``rules.py``/``indicators.py`` (which score *windowed, aggregated*
search trends). This maps a single DataLab observation (one
``datalab_raw_documents`` row: one keyword/date/period) to one ``signal_event``
per mapped stock so the queue Normalizer can populate
``source_documents``/``signal_events``. Pure functions, no clock, no DB.

The per-observation direction is intentionally coarse and polarity-aware:
- demand keyword (수요/관심): rising search = bullish.
- risk keyword (리콜/불매/논란…): rising search = bearish.
The real momentum/magnitude scoring happens later in the windowed
``DataLabAnalyzer`` (see ``indicators.py``); a spike here just rides impact up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

EVENT_TYPE_OBSERVATION = "datalab_search_observation"
EVENT_TYPE_SPIKE = "datalab_search_spike"

# Magnitude (abs change_pct) at/above which a non-spike observation is "medium"
# impact. Coarse on purpose — the windowed analyzer does the real scoring.
_MEDIUM_CHANGE_PCT = 20.0


@dataclass(frozen=True)
class DataLabEventClassification:
    event_type: str
    signal_direction: str  # positive | neutral | negative | mixed | unknown
    impact_level: str  # high | medium | low
    needs_review: bool = False


def classify_datalab_observation(row: Mapping[str, Any]) -> DataLabEventClassification:
    """Classify one DataLab observation row into a signal-event shape."""
    polarity = str(row.get("polarity") or "demand").strip().lower()
    change_pct = _to_float(row.get("change_pct"))
    is_spike = _truthy(row.get("is_spike"))

    if change_pct is None or change_pct == 0:
        direction = "neutral"
    else:
        rising = change_pct > 0
        if polarity == "risk":
            direction = "negative" if rising else "positive"
        else:
            direction = "positive" if rising else "negative"

    if is_spike:
        impact_level = "high"
    elif change_pct is not None and abs(change_pct) >= _MEDIUM_CHANGE_PCT:
        impact_level = "medium"
    else:
        impact_level = "low"

    return DataLabEventClassification(
        event_type=EVENT_TYPE_SPIKE if is_spike else EVENT_TYPE_OBSERVATION,
        signal_direction=direction,
        impact_level=impact_level,
    )


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "t"}
