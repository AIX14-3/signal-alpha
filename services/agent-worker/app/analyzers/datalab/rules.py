"""Deterministic mapping from DataLab indicators to a signed signal.

Pure functions, no LLM, no clock. Scores are component sums clamped to [-1, +1].
Thresholds and weights come from ``DataLabRuleConfig`` — nothing hardcoded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.analyzers.config import DataLabRuleConfig
from app.analyzers.datalab.indicators import DataLabIndicators
from app.analyzers.scoring import graded

Direction = str  # "positive" | "neutral" | "negative" | "mixed" | "unknown"

MIXED_COMPONENT_THRESHOLD = 0.2


@dataclass(frozen=True)
class DataLabAssessment:
    direction: Direction
    score: float
    risk_flags: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)


def evaluate_indicators(
    indicators: DataLabIndicators,
    config: DataLabRuleConfig | None = None,
) -> DataLabAssessment:
    config = config or DataLabRuleConfig.from_env()

    if indicators.observations == 0:
        return DataLabAssessment(
            direction="unknown",
            score=0.0,
            risk_flags=["no_data"],
            highlights=["수집된 검색 트렌드 데이터가 없습니다."],
        )

    risk_flags: list[str] = []
    highlights: list[str] = []

    if indicators.observations < config.min_observations:
        risk_flags.append("insufficient_history")
        highlights.append(
            f"검색 트렌드 관측 {indicators.observations}건 — 분석 최소치({config.min_observations}) 미만"
        )
    if (
        indicators.days_since_latest is not None
        and indicators.days_since_latest > config.stale_days
    ):
        risk_flags.append("stale_data")
        highlights.append(
            f"최근 관측이 {indicators.days_since_latest}일 전 — {config.stale_days}일 기준 정체"
        )

    momentum = _momentum_component(indicators, config, highlights)
    spike = _spike_component(indicators, config, risk_flags, highlights)
    change = _change_component(indicators, config, highlights)
    risk = _risk_component(indicators, config, risk_flags, highlights)

    # Small-sample guard: too few prior-window points make the recent-vs-prior
    # ratio unreliable. Suppress momentum/change (ratio-based) but keep spike,
    # which is a recent-activity fraction, not a base-sensitive ratio.
    if indicators.prior_observations < config.min_prior_observations:
        momentum = 0.0
        change = 0.0
        risk_flags.append("low_base")
        highlights.append(
            f"이전 구간 관측 {indicators.prior_observations}건 < {config.min_prior_observations} "
            "— 표본 부족으로 모멘텀·변화율 억제"
        )

    score = max(-1.0, min(1.0, momentum + spike + change + risk))
    direction = _direction(score, momentum, indicators, config)
    return DataLabAssessment(
        direction=direction,
        score=round(score, 3),
        risk_flags=risk_flags,
        highlights=highlights,
    )


def _momentum_component(
    indicators: DataLabIndicators,
    config: DataLabRuleConfig,
    highlights: list[str],
) -> float:
    momentum = indicators.momentum_pct
    if momentum is None:
        return 0.0
    score = graded(momentum, scale=config.momentum_scale, weight=config.momentum_weight)
    if score > 0:
        highlights.append(f"검색지수 최근 평균 {momentum * 100:+.0f}% — 관심 증가 (점수 {score:+.2f})")
    elif score < 0:
        highlights.append(f"검색지수 최근 평균 {momentum * 100:+.0f}% — 관심 감소 (점수 {score:+.2f})")
    return score


def _spike_component(
    indicators: DataLabIndicators,
    config: DataLabRuleConfig,
    risk_flags: list[str],
    highlights: list[str],
) -> float:
    # spike_ratio is one-sided (0..1); graded tanh maps it to 0..+spike_weight.
    score = graded(indicators.spike_ratio, scale=config.spike_scale, weight=config.spike_weight)
    if indicators.spike_ratio >= config.spike_threshold:
        risk_flags.append("search_spike")
    if score > 0.01:
        highlights.append(
            f"급등 관측 비중 {indicators.spike_ratio * 100:.0f}% — 검색 급증 (점수 {score:+.2f})"
        )
    return score


def _risk_component(
    indicators: DataLabIndicators,
    config: DataLabRuleConfig,
    risk_flags: list[str],
    highlights: list[str],
) -> float:
    """Rising RISK-keyword searches (리콜/불매/논란…) are bearish → negative score.

    Returns 0 when no risk keywords are mapped/collected (risk_momentum_pct None)
    or the risk prior window is too small to trust.
    """
    rm = indicators.risk_momentum_pct
    if rm is None or indicators.risk_prior_observations < config.min_prior_observations:
        return 0.0
    score = -graded(rm, scale=config.risk_scale, weight=config.risk_weight)
    if score < 0:
        risk_flags.append("risk_search")
        highlights.append(f"위험 키워드 검색 {rm * 100:+.0f}% — 악재 신호 (점수 {score:+.2f})")
    elif score > 0:
        highlights.append(f"위험 키워드 검색 {rm * 100:+.0f}% — 위험 완화 (점수 {score:+.2f})")
    return score


def _change_component(
    indicators: DataLabIndicators,
    config: DataLabRuleConfig,
    highlights: list[str],
) -> float:
    avg_change = indicators.avg_change_pct
    if avg_change is None:
        return 0.0
    score = graded(avg_change, scale=config.change_scale, weight=config.change_weight)
    if score > 0:
        highlights.append(f"평균 변화율 {avg_change:+.0f}% — 상승 추세 (점수 {score:+.2f})")
    elif score < 0:
        highlights.append(f"평균 변화율 {avg_change:+.0f}% — 하락 추세 (점수 {score:+.2f})")
    return score


def _direction(
    score: float,
    momentum: float,
    indicators: DataLabIndicators,
    config: DataLabRuleConfig,
) -> Direction:
    # Conflicting = search index trending DOWN (graded momentum meaningfully
    # negative, i.e. not guard-suppressed) WHILE spikes are present. Keyed off the
    # spike_ratio indicator so it's stable under the soft (tanh) component scaling.
    conflicting = (
        momentum <= -MIXED_COMPONENT_THRESHOLD
        and indicators.spike_ratio >= config.spike_threshold
    )
    if conflicting:
        return "mixed"
    if score >= config.positive_threshold:
        return "positive"
    if score <= config.negative_threshold:
        return "negative"
    return "neutral"
