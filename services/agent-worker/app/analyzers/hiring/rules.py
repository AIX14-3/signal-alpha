"""Deterministic mapping from hiring indicators to a signed signal.

Pure functions, no LLM, no clock. Scores are component sums clamped to [-1, +1].
Thresholds and weights come from ``HiringRuleConfig`` — nothing hardcoded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.analyzers.config import HiringRuleConfig
from app.analyzers.hiring.indicators import HiringIndicators
from app.analyzers.scoring import graded

Direction = Literal["positive", "neutral", "negative", "mixed", "unknown"]


@dataclass(frozen=True)
class HiringAssessment:
    direction: Direction
    score: float
    risk_flags: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)


def evaluate_indicators(
    indicators: HiringIndicators,
    config: HiringRuleConfig | None = None,
) -> HiringAssessment:
    config = config or HiringRuleConfig.from_env()

    if indicators.observations == 0:
        return HiringAssessment(
            direction="unknown",
            score=0.0,
            risk_flags=["no_data"],
            highlights=["수집된 채용 공고 데이터가 없습니다."],
        )

    risk_flags: list[str] = []
    highlights: list[str] = []

    if indicators.observations < config.min_observations:
        risk_flags.append("insufficient_history")
        highlights.append(
            f"채용 공고 관측 {indicators.observations}건 — 분석 최소치({config.min_observations}) 미만"
        )
    if (
        indicators.days_since_latest is not None
        and indicators.days_since_latest > config.stale_days
    ):
        risk_flags.append("stale_data")
        highlights.append(
            f"최근 공고가 {indicators.days_since_latest}일 전 — {config.stale_days}일 기준 정체"
        )

    momentum = _momentum_component(indicators, config, highlights)
    change = _change_component(indicators, config, highlights)

    # Small-sample guard: a tiny prior window makes momentum/change ratios
    # explode (e.g. 1 → 4 postings = +300%). Suppress both and flag low_base so
    # sparse early-stage hiring data does not produce phantom signals.
    if indicators.prior_observations < config.min_prior_observations:
        momentum = 0.0
        change = 0.0
        risk_flags.append("low_base")
        highlights.append(
            f"이전 구간 관측 {indicators.prior_observations}건 < {config.min_prior_observations} "
            "— 표본 부족으로 모멘텀·변화율 억제"
        )

    # Sector demand is a peer-based signal, independent of THIS stock's sample
    # size, so the low_base guard above does not suppress it.
    sector = _sector_demand_component(indicators, config, highlights)

    score = max(-1.0, min(1.0, momentum + change + sector))
    direction = _direction(score, config)
    return HiringAssessment(
        direction=direction,
        score=round(score, 3),
        risk_flags=risk_flags,
        highlights=highlights,
    )


def _momentum_component(
    indicators: HiringIndicators,
    config: HiringRuleConfig,
    highlights: list[str],
) -> float:
    momentum = indicators.momentum_pct
    if momentum is None:
        return 0.0
    score = graded(momentum, scale=config.momentum_scale, weight=config.momentum_weight)
    if score > 0:
        highlights.append(f"채용 규모 최근 평균 {momentum * 100:+.0f}% — 고용 확대 (점수 {score:+.2f})")
    elif score < 0:
        highlights.append(f"채용 규모 최근 평균 {momentum * 100:+.0f}% — 고용 축소 (점수 {score:+.2f})")
    return score


def _change_component(
    indicators: HiringIndicators,
    config: HiringRuleConfig,
    highlights: list[str],
) -> float:
    avg_change = indicators.avg_change_pct
    if avg_change is None:
        return 0.0
    score = graded(avg_change, scale=config.change_scale, weight=config.change_weight)
    if score > 0:
        highlights.append(f"공고 수 평균 변화율 {avg_change:+.0f}% — 증가 추세 (점수 {score:+.2f})")
    elif score < 0:
        highlights.append(f"공고 수 평균 변화율 {avg_change:+.0f}% — 감소 추세 (점수 {score:+.2f})")
    return score


def _sector_demand_component(
    indicators: HiringIndicators,
    config: HiringRuleConfig,
    highlights: list[str],
) -> float:
    """Sector-wide demand momentum for the functions this stock depends on.

    Returns 0 — exact own-momentum fallback — when the stock has no function
    mapping or peers have no comparable data (sector_demand_momentum None).
    """
    momentum = indicators.sector_demand_momentum
    if momentum is None or indicators.sector_demand_coverage <= 0:
        return 0.0
    score = graded(momentum, scale=config.sector_demand_scale, weight=config.sector_demand_weight)
    if score > 0:
        highlights.append(
            f"동종업 직군 수요 {momentum * 100:+.0f}% — 섹터 채용 확대 전파 (점수 {score:+.2f})"
        )
    elif score < 0:
        highlights.append(
            f"동종업 직군 수요 {momentum * 100:+.0f}% — 섹터 채용 위축 전파 (점수 {score:+.2f})"
        )
    return score


def _direction(score: float, config: HiringRuleConfig) -> Direction:
    if score >= config.positive_threshold:
        return "positive"
    if score <= config.negative_threshold:
        return "negative"
    return "neutral"
