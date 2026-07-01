"""Shared situation-text builder for episodic memory.

The writer (store) and recall (query) MUST embed *byte-identical* text for the
same situation, or the cosine neighborhood is meaningless. Both therefore go
through :func:`build_situation_text`. The text is a compact, deterministic
rendering of "who fired, which way, how strongly, on what evidence" — no free
LLM prose, so the embedding is reproducible.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Fixed source order so the rendered text (hence the embedding) is stable
# regardless of dict iteration order.
_SOURCE_ORDER = ("DART", "PRICE", "REPORT", "HIRING", "PATENT", "DATALAB")


@dataclass(frozen=True)
class SituationInput:
    """Everything the situation embedding is built from.

    ``breakdown`` is the aggregator's ``score_breakdown`` (per-source direction/
    score/status/summary). ``signal``/``final_score`` are display context only —
    they describe the situation, they are NOT what recall reasons *about*.
    """

    signal: str
    breakdown: Mapping[str, Any]


def build_situation_text(situation: SituationInput) -> str:
    """Render a deterministic natural-language description of the situation.

    One line per source that actually ran (missing sources are skipped so a
    "3-source" situation never looks like a "6-source" one). Korean framing uses
    "흔적/근거" wording, never buy/sell/confidence.
    """
    lines = [f"종합 방향 흔적: {_kor_direction(situation.signal)}"]
    for source in _SOURCE_ORDER:
        entry = situation.breakdown.get(source)
        if not isinstance(entry, Mapping):
            continue
        status = str(entry.get("data_status") or "")
        if status in ("", "missing"):
            continue
        direction = _kor_direction(entry.get("direction"))
        score = entry.get("score")
        score_text = f"{float(score):+.2f}" if isinstance(score, (int, float)) else "-"
        summary = entry.get("summary")
        summary_text = f" · 근거: {str(summary).strip()}" if summary else ""
        lines.append(f"{source} 발화: 방향 {direction}, 점수 {score_text}{summary_text}")
    return "\n".join(lines)


def _kor_direction(value: Any) -> str:
    text = str(value or "neutral").strip().lower()
    return {
        "positive": "긍정 흔적",
        "negative": "주의 흔적",
        "mixed": "혼재",
        "neutral": "중립",
        "unknown": "미상",
    }.get(text, "중립")
