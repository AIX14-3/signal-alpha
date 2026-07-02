"""Disagreement detection — the deterministic trigger for conditional re-query.

The orchestrator only pays for a re-query round when the assembled fan-in looks
*unreliable*: sources disagree (low ``_agreement_rate`` / ``mixed``) or coverage
is thin. This module turns the already-computed aggregate meta (which
``_aggregate`` produced) into a boolean gate plus the concrete list of "problem"
sources to re-ask — so 80–90% of stocks skip the LLM round entirely (plan §2.2).

This is pure/deterministic: no LLM, no DB. It never touches the headline number.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

# Only sources that can actually be re-queried (have an alternative registration
# + already-collected raw data to re-interpret). DART/PRICE/REPORT are team-owned
# and/or have no re-query seam yet, so they are never targeted — they are skipped
# structurally, not errored (plan: "agent_factory/RuleSourceAgent 있는 것만으로 동작").
REQUERYABLE_SOURCES = ("HIRING", "PATENT", "DATALAB")

# Agreement at or below this fraction is treated as disagreement worth re-asking.
DISAGREEMENT_AGREEMENT_MAX = 0.5


@dataclass(frozen=True)
class DisagreementVerdict:
    should_requery: bool
    reasons: list[str] = field(default_factory=list)
    target_sources: list[str] = field(default_factory=list)
    # Structured re-query focus — *what/which axis* is uncertain, per target source
    # (additive; empty when nothing to re-ask). The re-query path threads a per-source
    # slice of this into the source agent's input ``context`` so an agent that reads
    # it can narrow its re-read; agents that don't are byte-identical. See ``_build_focus``.
    focus: dict = field(default_factory=dict)


def detect_disagreement(
    *,
    signal: str,
    source_agreement: str,
    breakdown: dict[str, dict],
    agreement_rate: float,
    available_source_count: int,
) -> DisagreementVerdict:
    """Decide whether (and which sources) to re-query.

    Triggers on any of: a ``mixed`` headline direction, LOW cross-source
    agreement (with ≥2 sources present), or an agreement_rate at/below
    ``DISAGREEMENT_AGREEMENT_MAX``. A single-source stock never triggers (nothing
    to reconcile). Targets are the re-queryable sources whose direction diverges
    from the consensus / carry review flags — the ones whose re-interpretation
    could actually move the disagreement.
    """
    reasons: list[str] = []
    if signal == "mixed":
        reasons.append("mixed_direction")
    if available_source_count >= 2:
        if source_agreement == "LOW":
            reasons.append("low_source_agreement")
        if agreement_rate <= DISAGREEMENT_AGREEMENT_MAX:
            reasons.append("agreement_rate_below_threshold")

    if not reasons:
        return DisagreementVerdict(should_requery=False)

    targets = _target_sources(breakdown)
    if not targets:
        # Disagreement exists but none of it is on a re-queryable source (e.g. a
        # DART vs PRICE split). Structurally skip — no round is spent.
        return DisagreementVerdict(
            should_requery=False,
            reasons=[*reasons, "no_requeryable_target"],
        )
    deduped_reasons = _dedupe(reasons)
    return DisagreementVerdict(
        should_requery=True,
        reasons=deduped_reasons,
        target_sources=targets,
        focus=_build_focus(
            breakdown=breakdown,
            targets=targets,
            signal=signal,
            reasons=deduped_reasons,
        ),
    )


def _build_focus(
    *,
    breakdown: dict[str, dict],
    targets: list[str],
    signal: str,
    reasons: list[str],
) -> dict:
    """Turn the disagreement into a concrete "what to re-check" hint per source.

    Pure/deterministic (no LLM, no numbers): reads the already-computed per-source
    breakdown and states, for each target, *which axis looks uncertain* — the
    source's own direction, whether it diverges from the blended headline, and any
    review/risk flags it carries — plus a short human-readable ``ask``. This is the
    evidence the re-query threads to the source agent so a focused re-read has a
    target; it never touches score/direction (owned by the meta-learner).
    """
    per_source: dict[str, dict] = {}
    for source in targets:
        entry = breakdown.get(source)
        if not isinstance(entry, dict):
            continue
        direction = str(entry.get("direction") or "").lower() or "unknown"
        needs_review = bool(entry.get("needs_review"))
        risk_flags = entry.get("risk_flags")
        risk_flags = list(risk_flags) if isinstance(risk_flags, (list, tuple)) else []
        diverges = signal == "mixed" or (
            direction in ("negative", "mixed") and signal in ("positive", "neutral")
        )
        per_source[source] = {
            "direction": direction,
            "diverges_from_headline": diverges,
            "headline_signal": signal,
            "needs_review": needs_review,
            "risk_flags": risk_flags,
            "ask": _focus_ask(source, direction, signal, needs_review, risk_flags),
        }
    if not per_source:
        return {}
    return {
        "reasons": list(reasons),
        "headline_signal": signal,
        "sources": per_source,
    }


def _focus_ask(
    source: str,
    direction: str,
    signal: str,
    needs_review: bool,
    risk_flags: list[str],
) -> str:
    """Short Korean re-read prompt for the flagged source (display/agent hint only)."""
    parts = [
        f"{source} 재검토 요청: 방향({direction})이 종합 헤드라인({signal})과 어긋나는지 "
        "근거(원자료)를 다시 확인해 주세요."
    ]
    if needs_review:
        parts.append("이 소스는 needs_review 로 표시되어 있습니다.")
    if risk_flags:
        parts.append(f"주의 플래그: {', '.join(risk_flags)}.")
    return " ".join(parts)


def _target_sources(breakdown: dict[str, dict]) -> list[str]:
    """Re-queryable sources whose read is uncertain (diverging / flagged / mixed).

    Only sources that ran (``data_status`` not missing/failed) are candidates: a
    missing source has no raw data to re-interpret. We prefer sources whose
    direction is negative/mixed or that carry needs_review, since those are the
    ones a focused re-read might clarify; if none qualify we fall back to every
    re-queryable source that ran.
    """
    ran: list[str] = []
    diverging: list[str] = []
    for source in REQUERYABLE_SOURCES:
        entry = breakdown.get(source)
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("data_status") or "")
        if status in ("", "missing", "failed"):
            continue
        ran.append(source)
        direction = str(entry.get("direction") or "").lower()
        if direction in ("negative", "mixed") or entry.get("needs_review"):
            diverging.append(source)
    return diverging or ran


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
