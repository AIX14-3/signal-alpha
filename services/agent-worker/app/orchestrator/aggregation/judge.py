"""Judge step — fold recalled episodes into the evidence framing (reference only).

The deterministic ``_aggregate`` already produced positive_evidence /
caution_evidence / needs_review. The judge is where the orchestrator's episodic
memory (recalled similar past situations) is attached as a *reference* the
downstream synthesis LLM may cite — it never edits the headline number/direction
(owned by the meta-learner) nor the deterministic score fields.

Framing follows the product rules: "긍정 근거 / 주의 근거", never buy/sell/
confidence. Recalled episodes surface as ``memory_reference`` items, tagged with
whether the past episode's *recorded outcome* (if any) later aligned — so
needs_review calibration can lean on the product's own history, not model priors.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def build_memory_reference(episodes: Sequence[Any]) -> list[dict[str, Any]]:
    """Turn recalled episodes into display-only reference items.

    Each item is a compact, self-describing note ("과거 유사 상황"): direction,
    similarity, and — only if the episode already has a recorded outcome — a short
    outcome note. No numbers here feed scoring; these are 참고 for reasoning.
    """
    reference: list[dict[str, Any]] = []
    for episode in episodes:
        item: dict[str, Any] = {
            "kind": "past_similar_situation",
            "signal_date": _iso(getattr(episode, "signal_date", None)),
            "direction": getattr(episode, "direction", None),
            "similarity": getattr(episode, "similarity", None),
        }
        outcome = getattr(episode, "outcome", None)
        if outcome:
            item["outcome"] = outcome
        note = _episode_note(item, outcome)
        if note:
            item["note"] = note
        reference.append(item)
    return reference


def judge_with_memory(
    aggregate: Mapping[str, Any],
    memory_reference: Sequence[dict[str, Any]],
    *,
    disagreement_reasons: Sequence[str] = (),
    requery_rounds: int = 0,
) -> dict[str, Any]:
    """Return the aggregate augmented with memory reference + orchestration meta.

    Pure merge: copies the deterministic aggregate and *adds* reference fields.
    It does NOT alter signal / final_score / consensus_score / source_agreement —
    those stay exactly as ``_aggregate`` computed them (invariant: LLM/memory
    never make the number). ``needs_review`` can only be raised, never lowered:
    a re-query round or a divergent memory recall is a reason to look closer, not
    a reason to wave a signal through.
    """
    judged = dict(aggregate)
    if memory_reference:
        judged["memory_reference"] = list(memory_reference)
    if disagreement_reasons:
        judged["orchestration"] = {
            "disagreement_reasons": list(disagreement_reasons),
            "requery_rounds": int(requery_rounds),
        }
    # Memory/orchestration may only *raise* the review flag, never clear it.
    if bool(aggregate.get("needs_review")) or _memory_warrants_review(memory_reference):
        judged["needs_review"] = True
    return judged


def _memory_warrants_review(memory_reference: Sequence[dict[str, Any]]) -> bool:
    """A recalled episode whose recorded outcome was adverse nudges review on.

    Conservative: only an explicit adverse/false-positive outcome tag counts, so a
    cold-start (no outcomes yet) never changes behaviour. This is calibration, not
    a numeric input — it can flip needs_review but never the headline direction.
    """
    for item in memory_reference:
        outcome = item.get("outcome")
        if isinstance(outcome, Mapping):
            label = str(outcome.get("label") or outcome.get("result") or "").lower()
            if label in ("adverse", "false_positive", "miss", "wrong"):
                return True
    return False


def _episode_note(item: Mapping[str, Any], outcome: Any) -> str | None:
    direction = item.get("direction") or "중립"
    if isinstance(outcome, Mapping):
        label = outcome.get("label") or outcome.get("result")
        if label:
            return f"과거 유사 상황({direction})의 기록된 결과: {label}"
    return f"과거 유사 상황({direction}) — 결과 미기록(참고)"


def _iso(value: Any) -> Any:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)
