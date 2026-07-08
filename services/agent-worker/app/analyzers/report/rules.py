"""Deterministic mapping from broker-report valuation to a signed signal.

Pure functions, no LLM, no clock. Scores are component sums clamped to [-1, +1];
the 0-100 mapping is the aggregation layer's job. Direction comes from the
**target price**, not the buy-biased investment opinion:

- revision: latest target price vs the prior report's target (up/down revision).
- upside: latest target price vs current close (softened for buy-bias).

Both are one graded(tanh) component each. When neither is computable (no prior
target and no current price) the assessment is ``unknown`` → the analyzer keeps
its features-only ``no_signal`` fallback (exact prior behavior).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.analyzers.config import ReportRuleConfig
from app.analyzers.scoring import graded

Direction = str  # "positive" | "neutral" | "negative" | "unknown"


@dataclass(frozen=True)
class ReportAssessment:
    direction: Direction
    score: float
    has_signal: bool
    risk_flags: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)


def evaluate_report(
    *,
    revision_pct: float | None,
    upside_pct: float | None,
    config: ReportRuleConfig | None = None,
) -> ReportAssessment:
    """(revision_pct, upside_pct) → signed report signal.

    ``revision_pct`` = (latest_target - prior_target) / prior_target (signed).
    ``upside_pct``   = (latest_target - current_close) / current_close (signed).
    Either may be None (missing prior target / missing price) — the present ones
    are summed. All-None → ``unknown`` (no signal).
    """
    config = config or ReportRuleConfig.from_env()

    components = 0.0
    used = False
    risk_flags: list[str] = []
    highlights: list[str] = []

    if revision_pct is not None:
        score = graded(revision_pct, scale=config.revision_scale, weight=config.revision_weight)
        components += score
        used = True
        if score > 0:
            highlights.append(f"목표주가 {revision_pct * 100:+.1f}% 상향 (점수 {score:+.2f})")
        elif score < 0:
            highlights.append(f"목표주가 {revision_pct * 100:+.1f}% 하향 (점수 {score:+.2f})")

    if upside_pct is not None:
        score = graded(upside_pct, scale=config.upside_scale, weight=config.upside_weight)
        components += score
        used = True
        if abs(score) > 0.01:
            highlights.append(f"현재가 대비 목표가 괴리 {upside_pct * 100:+.1f}% (점수 {score:+.2f})")

    if not used:
        return ReportAssessment(
            direction="unknown",
            score=0.0,
            has_signal=False,
            risk_flags=["no_valuation_signal"],
            highlights=["목표주가 리비전·괴리를 산출할 데이터가 없습니다(직전 목표가·현재가 부재)."],
        )

    score = round(max(-1.0, min(1.0, components)), 3)
    return ReportAssessment(
        direction=_direction(score, config),
        score=score,
        has_signal=True,
        risk_flags=risk_flags,
        highlights=highlights,
    )


def compute_revision_pct(
    latest_target: float | None,
    prior_target: float | None,
    *,
    min_target_price: float,
) -> float | None:
    if latest_target is None or prior_target is None:
        return None
    if prior_target < min_target_price:
        return None
    return (latest_target - prior_target) / prior_target


def compute_upside_pct(
    latest_target: float | None,
    current_close: float | None,
    *,
    min_price: float,
) -> float | None:
    if latest_target is None or current_close is None:
        return None
    if current_close < min_price:
        return None
    return (latest_target - current_close) / current_close


def _direction(score: float, config: ReportRuleConfig) -> Direction:
    if score >= config.positive_threshold:
        return "positive"
    if score <= config.negative_threshold:
        return "negative"
    return "neutral"
