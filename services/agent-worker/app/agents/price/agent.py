"""PRICE 분석 에이전트 — Tier C(결정론/LLM 없음): 기존 규칙 분석기를 계약으로 감싸기만 한다.

``PriceAnalyzer`` 는 이미 ``Analyzer`` 프로토콜(``analyze(stock_code, evidence) ->
SourceResult``)을 구현하고, ``RuleSourceAgent`` 가 그 프로토콜을 ``SourceAgentInput`` /
``SourceAgentOutput`` 계약으로 감싸는 범용 투명 어댑터다. 따라서 PRICE 는 DART(``events`` 기반이라
전용 클래스가 필요)와 달리 신규 매핑 로직 없이 ``RuleSourceAgent(PriceAnalyzer())`` 로 계약 peer 가
된다.

점수/방향/플래그는 규칙 분석기 소유로 그대로 흐른다(``RuleSourceAgent`` 가 보장) — 이 모듈은 LLM 을
쓰지 않는다.
"""

from __future__ import annotations

from app.agents.base import SourceAnalysisAgent
from app.agents.rule_source_agent import RuleSourceAgent
from app.analyzers.price.analyzer import PriceAnalyzer

# orchestrator/price/tasks.py 의 PRICE_VERSION 과 동일 값 — 저장 prompt_ver 와 일치시킨다.
PRICE_PROMPT_VERSION = "price-rules-v1"


def build_price_agent() -> SourceAnalysisAgent:
    """규칙 기반 ``PriceAnalyzer`` 를 계약 peer 로 감싼 에이전트를 반환한다."""
    return RuleSourceAgent(PriceAnalyzer(), prompt_ver=PRICE_PROMPT_VERSION)
