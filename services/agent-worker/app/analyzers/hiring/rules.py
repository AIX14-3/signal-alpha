"""Deterministic mapping from hiring indicators to a signed signal.

Pure functions, no LLM, no clock. Scores are component sums clamped to [-1, +1].
Thresholds and weights come from ``HiringRuleConfig`` — nothing hardcoded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.analyzers.config import HiringRuleConfig
from app.analyzers.hiring.indicators import DecayedHiringIndicators, HiringIndicators
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

    # OCR skill breadth is a content signal (what tech the company hires for),
    # independent of sample size, so the low_base guard does not suppress it.
    skill = _skill_component(indicators, config, highlights)

    score = max(-1.0, min(1.0, momentum + change + sector + skill))
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


def _skill_component(
    indicators: HiringIndicators,
    config: HiringRuleConfig,
    highlights: list[str],
) -> float:
    """Reward hiring across a breadth of concrete in-demand tech skills.

    Returns 0 — exact pre-enrichment fallback — when too few (or no) postings in
    the window have been OCR-enriched, so unenriched stocks score exactly as
    before. One-sided positive: a wide tech stack lifts the score; it never pushes
    negative. Mirrors the patent significance component.
    """
    if (
        not indicators.distinct_skills
        or indicators.skill_enriched_observations < config.skill_min_enriched
    ):
        return 0.0
    score = graded(
        float(indicators.distinct_skills),
        scale=config.skill_scale,
        weight=config.skill_weight,
    )
    if score > 0.01:
        preview = ", ".join(indicators.top_skills)
        highlights.append(
            f"채용 기술스택 {indicators.distinct_skills}종 (OCR: {preview}) "
            f"— 기술 투자 폭 (점수 {score:+.2f})"
        )
    return score


def _direction(score: float, config: HiringRuleConfig) -> Direction:
    if score >= config.positive_threshold:
        return "positive"
    if score <= config.negative_threshold:
        return "negative"
    return "neutral"


def evaluate_decayed(
    indicators: DecayedHiringIndicators,
    config: HiringRuleConfig | None = None,
    *,
    activity_scale: float | None = None,
) -> HiringAssessment:
    """공고별 시간감쇠 활동강도 → verdict(opt-in 경로).

    유효창 내 활성 공고가 없으면 no_signal(집계 제외 → per-source 미발행 → FE 만료).
    있으면 감쇠활동합을 graded(tanh)로 one-sided 양수 매핑(채용활동 강도). 하락(감소)
    → negative 는 per-company 베이스라인이 필요해 후속으로 남긴다.
    """
    config = config or HiringRuleConfig.from_env()

    if indicators.active_count == 0:
        return HiringAssessment(
            direction="unknown",
            score=0.0,
            risk_flags=["no_active_postings"],
            highlights=["유효창 내 활성 채용공고가 없습니다."],
        )

    risk_flags: list[str] = []
    highlights: list[str] = []
    scale = activity_scale if activity_scale is not None else config.activity_scale

    if indicators.active_count < config.min_observations:
        risk_flags.append("insufficient_history")
        highlights.append(
            f"활성 공고 {indicators.active_count}건 — 분석 최소치({config.min_observations}) 미만"
        )
    if indicators.top_skills:
        highlights.append(f"채용 기술스택: {', '.join(indicators.top_skills)}")

    # 옵션1: level + 급감 감지. 최근절반 활동이 직전절반의 임계 미만이면 채용 급감 → negative.
    # baseline_mode 로 추후 "long_term_avg"(옵션3) 교체 가능.
    declining = (
        config.baseline_mode == "prior_window"
        and indicators.prior_half_decayed > 0
        and indicators.recent_half_decayed
        < indicators.prior_half_decayed * config.decline_ratio_threshold
    )
    if declining:
        drop = indicators.prior_half_decayed - indicators.recent_half_decayed
        score = -round(min(1.0, graded(drop, scale=scale, weight=1.0)), 3)
        risk_flags.append("hiring_decline")
        highlights.append(
            f"채용 급감: 최근절반 {indicators.recent_half_decayed:.2f} < 직전절반 "
            f"{indicators.prior_half_decayed:.2f}(임계 {config.decline_ratio_threshold:.0%}) → 점수 {score:+.2f}"
        )
        direction: Direction = "negative" if score <= config.negative_threshold else "neutral"
    else:
        score = round(max(0.0, min(1.0, graded(indicators.decayed_activity, scale=scale, weight=1.0))), 3)
        highlights.append(
            f"감쇠 채용활동 {indicators.decayed_activity:.2f}"
            f"(활성 {indicators.active_count}건, 최근 {indicators.days_since_latest}일 전) → 점수 {score:+.2f}"
        )
        direction = "positive" if score >= config.positive_threshold else "neutral"

    return HiringAssessment(
        direction=direction,
        score=score,
        risk_flags=risk_flags,
        highlights=highlights,
    )
