"""DART B-lite 결정론 채점 수식 — 임팩트 가중 순극성 → graded(tanh) → 방향.

수식 채점 본체 — 서빙은 LLM 채점(SCORE_COHORT)으로 전환됨(2026-07-13 불변식 폐기).
이 코드는 백테스트/IC 계측기 + LLM 폴백(LLM_SCORING_FALLBACK=rules)으로 보존.

여기에는 **수식만** 있다. DART 이벤트 순회·method_detail/summary 조립(SourceResult 조립)은
``app.analyzers.dart.source_result`` 에 그대로 남아 있고, 그 모듈이 이 수식을 import 해 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analyzers.config import DartRuleConfig
from app.backtest.reference_rules.scoring import graded

# 임팩트 레벨 → 수치 가중(매그니튜드 프록시). 미지값은 0.
_IMPACT_WEIGHTS = {"high": 3.0, "medium": 2.0, "low": 1.0}


def impact_weight(level: str) -> float:
    return _IMPACT_WEIGHTS.get(level, 0.0)


@dataclass(frozen=True)
class DartPolarityScore:
    """임팩트 가중 순극성 채점 결과 — score/direction/data_status 3요소."""

    score: float
    direction: str  # "positive" | "neutral" | "negative" | "unknown"
    data_status: str  # "ok"(블렌드 산입) | "no_signal"(features-only 폴백)


def score_impact_weighted_polarity(
    positive_weight: float,
    negative_weight: float,
    config: DartRuleConfig | None = None,
) -> DartPolarityScore:
    """B-lite 결정론 점수: 임팩트 가중 순극성 = (Σ긍정_w − Σ부정_w) / Σ방향_w → graded(tanh).

    방향(positive/negative) 이벤트가 하나도 없으면(가중합 0) 기존 features-only 폴백
    (unknown/0.0/no_signal)을 반환한다.
    """
    directional_weight = positive_weight + negative_weight
    if directional_weight <= 0:
        return DartPolarityScore(score=0.0, direction="unknown", data_status="no_signal")
    net = (positive_weight - negative_weight) / directional_weight
    config = config or DartRuleConfig.from_env()
    score = round(graded(net, scale=config.polarity_scale, weight=config.polarity_weight), 3)
    if score >= config.positive_threshold:
        direction = "positive"
    elif score <= config.negative_threshold:
        direction = "negative"
    else:
        direction = "neutral"
    return DartPolarityScore(score=score, direction=direction, data_status="ok")
