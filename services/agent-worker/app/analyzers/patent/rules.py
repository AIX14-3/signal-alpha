"""DEPRECATED façade — 수식 본체는 ``app.backtest.reference_rules.patent`` 로 이동(2026-07-13).

서빙 점수는 LLM 채점(SCORE_COHORT)으로 전환됐다. 이 모듈은 기존 import 경로를 깨지 않기
위한 재수출 껍데기다(서빙 폴백 LLM_SCORING_FALLBACK=rules 포함) — 새 코드는
``app.backtest.reference_rules.patent`` 를 직접 import 할 것.
"""

from app.backtest.reference_rules.patent import (
    MIXED_COMPONENT_THRESHOLD,
    Direction,
    PatentAssessment,
    _activity_component,
    _direction,
    _momentum_component,
    _new_category_component,
    _recency_factor,
    _significance_component,
    evaluate_indicators,
)

__all__ = [
    "MIXED_COMPONENT_THRESHOLD",
    "Direction",
    "PatentAssessment",
    "_activity_component",
    "_direction",
    "_momentum_component",
    "_new_category_component",
    "_recency_factor",
    "_significance_component",
    "evaluate_indicators",
]
