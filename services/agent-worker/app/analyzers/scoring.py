"""DEPRECATED façade — 수식 본체는 ``app.backtest.reference_rules.scoring`` 으로 이동(2026-07-13).

서빙 점수는 LLM 채점(SCORE_COHORT)으로 전환됐다. 이 모듈은 기존 import 경로
(``from app.analyzers.scoring import graded``)를 깨지 않기 위한 재수출 껍데기다 —
새 코드는 ``app.backtest.reference_rules.scoring`` 을 직접 import 할 것.
"""

from app.backtest.reference_rules.scoring import graded

__all__ = ["graded"]
