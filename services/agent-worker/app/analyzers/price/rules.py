"""Deterministic mapping from indicators to a signed signal.

Mirrors the DART analyzer's style (`analyzers/dart/rules.py`): pure functions,
no LLM. Scores are component sums clamped to [-1, +1]; the 0–100 mapping is the
aggregation layer's job, not this module's. Wording stays descriptive — no
investment-advice phrasing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.analyzers.price.indicators import FULL_HISTORY_SESSIONS, MIN_SESSIONS, PriceIndicators

Direction = str  # "positive" | "neutral" | "negative" | "mixed" | "unknown"

RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
HIGH_VOLATILITY_PCT = 3.0
VOLUME_SPIKE_Z = 2.0
FLOW_STREAK_SESSIONS = 3
MIXED_COMPONENT_THRESHOLD = 0.2


@dataclass(frozen=True)
class PriceAssessment:
    direction: Direction
    score: float
    risk_flags: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)


def evaluate_indicators(indicators: PriceIndicators) -> PriceAssessment:
    if indicators.sessions < MIN_SESSIONS:
        return PriceAssessment(
            direction="unknown",
            score=0.0,
            risk_flags=["insufficient_history"],
            highlights=[f"가격 이력 {indicators.sessions}영업일 — 지표 산출 최소치({MIN_SESSIONS}) 미만"],
        )

    risk_flags: list[str] = []
    highlights: list[str] = []
    if indicators.sessions < FULL_HISTORY_SESSIONS:
        risk_flags.append("short_history")

    trend = _trend_component(indicators, highlights)
    momentum = _momentum_component(indicators, risk_flags, highlights)
    flow = _flow_component(indicators, highlights)

    if indicators.volatility20 is not None and indicators.volatility20 >= HIGH_VOLATILITY_PCT:
        risk_flags.append("high_volatility")
        highlights.append(f"20일 일간변동성 {indicators.volatility20:.1f}% — 변동성 확대 구간")
    if indicators.volume_z is not None and indicators.volume_z >= VOLUME_SPIKE_Z:
        risk_flags.append("volume_spike")
        highlights.append(f"거래량 z-score {indicators.volume_z:.1f} — 직전 20일 대비 급증")

    score = max(-1.0, min(1.0, trend + momentum + flow))
    direction = _direction(score, trend, flow)
    return PriceAssessment(
        direction=direction,
        score=round(score, 3),
        risk_flags=risk_flags,
        highlights=highlights,
    )


def _trend_component(indicators: PriceIndicators, highlights: list[str]) -> float:
    component = 0.0
    sma5, sma20, sma60 = indicators.sma5, indicators.sma20, indicators.sma60
    if sma5 is not None and sma20 is not None and sma60 is not None:
        if sma5 > sma20 > sma60:
            component += 0.3
            highlights.append("5/20/60일 이동평균 정배열")
        elif sma5 < sma20 < sma60:
            component -= 0.3
            highlights.append("5/20/60일 이동평균 역배열")
    if indicators.golden_cross:
        component += 0.15
        highlights.append("최근 3영업일 내 5/20일 골든크로스 발생")
    if indicators.dead_cross:
        component -= 0.15
        highlights.append("최근 3영업일 내 5/20일 데드크로스 발생")
    return component


def _momentum_component(
    indicators: PriceIndicators,
    risk_flags: list[str],
    highlights: list[str],
) -> float:
    rsi14 = indicators.rsi14
    if rsi14 is None:
        return 0.0
    if rsi14 >= RSI_OVERBOUGHT:
        risk_flags.append("overbought")
        highlights.append(f"RSI(14) {rsi14:.0f} — 과매수 구간")
        return -0.1
    if rsi14 <= RSI_OVERSOLD:
        risk_flags.append("oversold")
        highlights.append(f"RSI(14) {rsi14:.0f} — 과매도 구간")
        return 0.0
    if rsi14 >= 50.0:
        return 0.1
    return -0.1


def _flow_component(indicators: PriceIndicators, highlights: list[str]) -> float:
    component = 0.0
    if indicators.foreign_streak >= FLOW_STREAK_SESSIONS:
        component += 0.15
        highlights.append(f"외국인 {indicators.foreign_streak}영업일 연속 순매수")
    elif indicators.foreign_streak <= -FLOW_STREAK_SESSIONS:
        component -= 0.15
        highlights.append(f"외국인 {-indicators.foreign_streak}영업일 연속 순매도")
    if indicators.institution_streak >= FLOW_STREAK_SESSIONS:
        component += 0.1
        highlights.append(f"기관 {indicators.institution_streak}영업일 연속 순매수")
    elif indicators.institution_streak <= -FLOW_STREAK_SESSIONS:
        component -= 0.1
        highlights.append(f"기관 {-indicators.institution_streak}영업일 연속 순매도")

    foreign_sum = indicators.foreign_net_sum20
    institution_sum = indicators.institution_net_sum20
    if foreign_sum is not None and institution_sum is not None:
        if foreign_sum > 0 and institution_sum > 0:
            component += 0.1
            highlights.append("최근 20영업일 외국인·기관 동반 순매수")
        elif foreign_sum < 0 and institution_sum < 0:
            component -= 0.1
            highlights.append("최근 20영업일 외국인·기관 동반 순매도")
    return component


def _direction(score: float, trend: float, flow: float) -> Direction:
    conflicting = (
        trend * flow < 0
        and abs(trend) >= MIXED_COMPONENT_THRESHOLD
        and abs(flow) >= MIXED_COMPONENT_THRESHOLD
    )
    if conflicting:
        return "mixed"
    if score >= 0.2:
        return "positive"
    if score <= -0.2:
        return "negative"
    return "neutral"
