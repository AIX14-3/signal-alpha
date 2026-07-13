"""수식 채점 본체(reference rules) — 결정론 스코어러의 단일 거처.

서빙은 LLM 채점(SCORE_COHORT)으로 전환됨(2026-07-13 불변식 폐기). 이 패키지는
백테스트/IC 계측기(``app.backtest.reference_scorer`` → ``scripts/recompute_source_ic.py``)
+ LLM 폴백(LLM_SCORING_FALLBACK=rules)으로 보존된다.

기존 서빙 경로(``app/analyzers/*/rules.py``·``app/analyzers/scoring.py``)는 여기서
재수출하는 얇은 façade 다 — 호출측·테스트는 무변경으로 계속 동작한다(env 플래그 off 롤백 유지).

RuleConfig 클래스들은 순환 import 방지를 위해 ``app.analyzers.config`` 에 그대로 둔다.
"""

from app.backtest.reference_rules.dart_score import (
    DartPolarityScore,
    impact_weight,
    score_impact_weighted_polarity,
)
from app.backtest.reference_rules.datalab import DataLabAssessment
from app.backtest.reference_rules.datalab import evaluate_indicators as evaluate_datalab_indicators
from app.backtest.reference_rules.hiring import HiringAssessment, evaluate_decayed
from app.backtest.reference_rules.hiring import evaluate_indicators as evaluate_hiring_indicators
from app.backtest.reference_rules.patent import PatentAssessment
from app.backtest.reference_rules.patent import evaluate_indicators as evaluate_patent_indicators
from app.backtest.reference_rules.report import (
    ReportAssessment,
    compute_revision_pct,
    compute_upside_pct,
    evaluate_report,
)
from app.backtest.reference_rules.scoring import graded

__all__ = [
    "DartPolarityScore",
    "DataLabAssessment",
    "HiringAssessment",
    "PatentAssessment",
    "ReportAssessment",
    "compute_revision_pct",
    "compute_upside_pct",
    "evaluate_datalab_indicators",
    "evaluate_decayed",
    "evaluate_hiring_indicators",
    "evaluate_patent_indicators",
    "evaluate_report",
    "graded",
    "impact_weight",
    "score_impact_weighted_polarity",
]
