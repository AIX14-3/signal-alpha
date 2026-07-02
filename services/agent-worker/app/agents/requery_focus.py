"""Orchestrator re-query focus → LLM prompt hint (Wave-3 되묻기 실동작화).

The orchestrator's disagreement detector (``orchestrator/aggregation/detect.py``)
builds a per-source *focus* — *which axis* of a source's read looks uncertain —
and the re-query path threads it into the source agent's
``SourceAgentInput.context["requery_focus"]`` (#738 wired the transport). This
module is the **consumer** side: it turns that focus into a short human-readable
hint and a prompt block the opt-in LLM classify path appends, so a re-queried
agent narrows its *re-read / rationale* to the flagged axis.

Invariants:
  - **LLM-only.** Only the gated ``classify_*`` path reads this; the rule-only
    path never sees ``context`` → the deterministic score/direction are untouched.
  - **Numbers unchanged.** The hint steers *evidence/rationale* wording only; it
    never feeds score/direction (owned by the meta-learner). Focus is additive.
  - **Byte-identical degrade.** No focus (plain analyze), an empty/opaque focus,
    or any parse error → ``None`` hint → ``requery_focus_prompt_block`` returns
    ``""`` → the prompt is byte-identical to a non-re-query analyze. Never raises.

Focus shapes handled (degrade-safe, in priority order):
  - the structured per-source slice ``_focus_for_source`` emits: a mapping with a
    human-readable ``ask`` (built by ``detect._focus_ask``) plus axis fields
    (``direction`` / ``diverges_from_headline`` / ``needs_review`` / ``risk_flags``);
  - a legacy list/tuple of disagreement reason strings;
  - a bare string;
  - anything else / ``None`` → no hint.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def focus_hint_from_context(context: Any) -> str | None:
    """Human-readable re-query hint from a source agent input ``context``.

    Returns ``None`` when there is no re-query focus or on any parse failure, so
    the caller appends nothing and the prompt stays byte-identical. Never raises.
    """
    if not context:
        return None
    try:
        getter = getattr(context, "get", None)
        focus = getter("requery_focus") if callable(getter) else None
    except Exception:  # noqa: BLE001 — a malformed context must not block analysis
        return None
    return _hint_from_focus(focus)


def requery_focus_prompt_block(hint: str | None) -> str:
    """Prompt block instructing a focused re-read; ``""`` when there is no hint.

    Placed by each classifier's ``_build_prompt`` right before the JSON-output
    directive. Empty string when ``hint`` is falsy → the surrounding prompt is
    unchanged (byte-identical to the pre-focus build).
    """
    if not hint:
        return ""
    return (
        "[오케스트레이터 재질의 요청]\n"
        "종합 단계에서 아래 축이 불확실하다고 판단해 재확인을 요청했다. 원자료에서 해당\n"
        "부분을 다시 살펴 근거(rationale)를 재평가하라. 단, 점수·방향 등 숫자 판정은\n"
        "규칙이 소유하므로 절대 바꾸지 말고, 근거 서술만 이 축에 맞춰 좁혀라:\n"
        f"{hint}\n\n"
    )


# ---------------------------------------------------------------------------
# internals (pure — no LLM, no clock, never raise)
# ---------------------------------------------------------------------------
def _hint_from_focus(focus: Any) -> str | None:
    try:
        if focus is None:
            return None
        if isinstance(focus, str):
            text = focus.strip()
            return text or None
        if isinstance(focus, Mapping):
            return _hint_from_mapping(focus)
        if isinstance(focus, (list, tuple, set)):
            reasons = [str(item).strip() for item in focus if str(item).strip()]
            return ("재질의 사유: " + ", ".join(reasons)) if reasons else None
        return None
    except Exception:  # noqa: BLE001 — degrade to no-hint, never block the re-read
        return None


def _hint_from_mapping(focus: Mapping[str, Any]) -> str | None:
    # The structured focus always carries a ready human-readable ``ask``
    # (detect._focus_ask already folds in direction/headline/needs_review/risk_flags),
    # so prefer it verbatim. Fall back to composing the axes for a degraded/legacy
    # mapping that lacks ``ask``.
    ask = str(focus.get("ask") or "").strip()
    if ask:
        return ask

    parts: list[str] = []
    direction = str(focus.get("direction") or "").strip()
    headline = str(focus.get("headline_signal") or "").strip()
    if focus.get("diverges_from_headline") and direction:
        if headline:
            parts.append(
                f"이 소스의 방향({direction})이 종합 헤드라인({headline})과 어긋납니다. "
                "원자료에서 근거를 재확인하세요."
            )
        else:
            parts.append(
                f"이 소스의 방향({direction})이 종합 판단과 어긋납니다. 원자료에서 근거를 재확인하세요."
            )
    if focus.get("needs_review"):
        parts.append("이 소스는 needs_review 로 표시되어 있습니다.")
    risk_flags = focus.get("risk_flags")
    if isinstance(risk_flags, (list, tuple)) and risk_flags:
        parts.append("주의 플래그: " + ", ".join(str(flag) for flag in risk_flags) + ".")
    reasons = focus.get("reasons")
    if not parts and isinstance(reasons, (list, tuple)) and reasons:
        parts.append("재질의 사유: " + ", ".join(str(r).strip() for r in reasons if str(r).strip()))
    return " ".join(parts) if parts else None
