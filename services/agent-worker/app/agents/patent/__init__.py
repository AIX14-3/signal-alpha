"""Patent significance agent (기획 §2.1) — tier-B significance classifier.

Public seam:
  - ``PatentSignificanceAgent`` — the langgraph-free agent (rules + LLM materiality tag).
  - ``build_patent_agent`` — factory the source registry wires for PATENT when
    ``PATENT_LLM_ENABLED`` is on. Degrades to the plain rule path (``RuleSourceAgent``)
    when no Gemini key/client is available, so a missing key never changes behaviour
    vs. LLM-off.

Unlike the DataLab package this pulls in NO langgraph — tier B is a single gated
LLM call, not a graph.
"""

from __future__ import annotations

import logging
from os import getenv
from typing import Any

from app.agents.patent.agent import PatentSignificanceAgent
from app.agents.patent.llm_classifier import PatentSignificanceClassifier
from app.analyzers.config import PatentRuleConfig
from app.analyzers.patent import PatentAnalyzer
from app.core.config import get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "PatentSignificanceAgent",
    "PatentSignificanceClassifier",
    "build_patent_agent",
    "patent_llm_enabled",
]


def patent_llm_enabled() -> bool:
    """Whether the Patent significance agent should run (opt-in env flag)."""
    return str(getenv("PATENT_LLM_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}


def build_patent_agent(
    connection: Any = None,
    *,
    config: PatentRuleConfig | None = None,
    client: Any | None = None,
):
    """Build the Patent significance agent, or degrade to the rule path.

    Returns a ``PatentSignificanceAgent`` when an LLM client can be built;
    otherwise a transparent ``RuleSourceAgent`` so a missing key/transport leaves
    PATENT behaving exactly as the LLM-off rule path (no materiality tag, same
    score). ``connection`` is accepted for factory-signature parity with other
    sources (the registry calls ``agent_factory(connection)``) but patent needs no
    DB access beyond the evidence already loaded — so it is unused.
    """
    config = config or PatentRuleConfig.from_env()
    analyzer = PatentAnalyzer(config)

    client = client or _try_build_gemini()
    if client is None:
        from app.agents.rule_source_agent import RuleSourceAgent

        return RuleSourceAgent(analyzer)

    return PatentSignificanceAgent(
        analyzer=analyzer,
        classifier=PatentSignificanceClassifier(client),
        gate_threshold=config.positive_threshold,
    )


def _try_build_gemini() -> Any | None:
    if not get_settings().gemini_api_key:
        return None
    try:
        from app.clients.gemini_client import GeminiJsonClient

        return GeminiJsonClient(api_key=get_settings().gemini_api_key)
    except Exception as exc:  # noqa: BLE001 — missing key/transport: degrade, don't stall
        # 조용히 삼키면 significance 에이전트가 왜 규칙 경로로 떨어졌는지 알 수 없다
        # (hiring 팩토리와 동일하게 경고만 남기고 규칙 경로로 폴백).
        logger.warning("Patent significance LLM 클라이언트 미구성 (%s) — 규칙 경로로 폴백", exc)
        return None
