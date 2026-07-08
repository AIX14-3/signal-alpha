"""Deterministic mapping from patent indicators to a signed signal.

Pure functions, no LLM, no clock. Scores are component sums clamped to [-1, +1];
the 0-100 mapping is the aggregation/persistence layer's job. Thresholds and
weights come from ``PatentRuleConfig`` — nothing is hardcoded here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.analyzers.config import PatentRuleConfig
from app.analyzers.patent.indicators import PatentIndicators
from app.analyzers.scoring import graded

Direction = str  # "positive" | "neutral" | "negative" | "mixed" | "unknown"

MIXED_COMPONENT_THRESHOLD = 0.2


@dataclass(frozen=True)
class PatentAssessment:
    direction: Direction
    score: float
    risk_flags: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)


def evaluate_indicators(
    indicators: PatentIndicators,
    config: PatentRuleConfig | None = None,
) -> PatentAssessment:
    config = config or PatentRuleConfig.from_env()

    if indicators.total == 0:
        return PatentAssessment(
            direction="unknown",
            score=0.0,
            risk_flags=["no_data"],
            highlights=["수집된 특허 출원이 없습니다."],
        )

    risk_flags: list[str] = []
    highlights: list[str] = []

    if indicators.total < config.min_count:
        risk_flags.append("insufficient_history")
        highlights.append(
            f"특허 출원 {indicators.total}건 — 분석 최소치({config.min_count}) 미만, 신뢰도 낮음"
        )
    if (
        indicators.days_since_latest is not None
        and indicators.days_since_latest > config.stale_days
    ):
        risk_flags.append("stale_data")
        highlights.append(
            f"최근 공개가 {indicators.days_since_latest}일 전 — {config.stale_days}일 기준 정체"
        )

    momentum = _momentum_component(indicators, config, highlights)
    new_category = _new_category_component(indicators, config, highlights)
    activity = _activity_component(indicators, config, highlights)
    significance = _significance_component(indicators, config, highlights)

    score = max(-1.0, min(1.0, momentum + new_category + activity + significance))
    direction = _direction(score, momentum, indicators, config)
    return PatentAssessment(
        direction=direction,
        score=round(score, 3),
        risk_flags=risk_flags,
        highlights=highlights,
    )


def _momentum_component(
    indicators: PatentIndicators,
    config: PatentRuleConfig,
    highlights: list[str],
) -> float:
    ratio = indicators.momentum_ratio
    if ratio is None:
        if indicators.recent_count > 0:
            highlights.append(
                f"직전 구간 공개 없이 최근 {indicators.recent_count}건 신규 공개 — 활동 개시"
            )
            return config.momentum_weight
        return 0.0
    score = graded(ratio, scale=config.momentum_scale, weight=config.momentum_weight)
    if score > 0:
        highlights.append(
            f"최근 공개 {indicators.recent_count}건 vs 직전 {indicators.prior_count}건 — 공개 증가 (점수 {score:+.2f})"
        )
    elif score < 0:
        highlights.append(
            f"최근 공개 {indicators.recent_count}건 vs 직전 {indicators.prior_count}건 — 공개 감소 (점수 {score:+.2f})"
        )
    return score


def _new_category_component(
    indicators: PatentIndicators,
    config: PatentRuleConfig,
    highlights: list[str],
) -> float:
    # graded by the share of new tech categories (one-sided 0..+new_category_weight).
    score = graded(
        indicators.new_category_ratio,
        scale=config.new_category_scale,
        weight=config.new_category_weight,
    )
    if indicators.new_category_count > 0 and score > 0.01:
        highlights.append(
            f"신규 기술분류 {indicators.new_category_count}건 (비중 {indicators.new_category_ratio * 100:.0f}%) "
            f"— 신사업/기술 확장 신호 (점수 {score:+.2f})"
        )
    return score


def _significance_component(
    indicators: PatentIndicators,
    config: PatentRuleConfig,
    highlights: list[str],
) -> float:
    """Reward filing genuinely important patents, not just many patents.

    Returns 0 — exact pre-C3 fallback — when too few (or no) filings in the window
    have been LLM-enriched, so unenriched stocks score exactly as before. One-sided
    positive: high mean significance lifts the score; it never pushes negative.
    """
    if (
        indicators.mean_significance is None
        or indicators.llm_enriched_count < config.significance_min_enriched
    ):
        return 0.0
    score = graded(
        indicators.mean_significance,
        scale=config.significance_scale,
        weight=config.significance_weight,
    )
    if score > 0.01:
        highlights.append(
            f"LLM 중요도 평균 {indicators.mean_significance:.2f} "
            f"(최고 {indicators.max_significance:.2f}, {indicators.llm_enriched_count}건 농축) "
            f"— 핵심 특허 비중 (점수 {score:+.2f})"
        )
    return score


def _activity_component(
    indicators: PatentIndicators,
    config: PatentRuleConfig,
    highlights: list[str],
) -> float:
    # One-sided positive "sustained R&D" signal. Below the small-sample floor it
    # contributes 0 (same as before — the insufficient_history guard handles noise);
    # at/above it, graded by filing count so 3 vs 50 filings no longer score alike.
    # Stays single-sided (never negative): low activity is silence, not a bad signal.
    if indicators.total < config.min_count:
        return 0.0
    score = graded(
        float(indicators.total),
        scale=config.activity_scale,
        weight=config.activity_weight,
    )
    highlights.append(
        f"기간 내 공개 특허 {indicators.total}건 / 기술분류 {indicators.distinct_tech_categories}종 "
        f"— 지속적 R&D (점수 {score:+.2f})"
    )
    return score


def _direction(
    score: float,
    momentum: float,
    indicators: PatentIndicators,
    config: PatentRuleConfig,
) -> Direction:
    # Conflicting = filings trending DOWN (graded momentum meaningfully negative)
    # while NEW tech categories appear — declining output but new direction.
    conflicting = momentum <= -MIXED_COMPONENT_THRESHOLD and indicators.new_category_count > 0
    if conflicting:
        return "mixed"
    if score >= config.positive_threshold:
        return "positive"
    if score <= config.negative_threshold:
        return "negative"
    return "neutral"
