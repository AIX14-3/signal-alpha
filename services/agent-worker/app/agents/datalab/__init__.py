"""DataLab attention agent (협업안 §4) — LangGraph cause classifier.

Public seam:
  - ``DataLabAnalysisGraphAgent`` — the graph agent (rules + LLM cause tag).
  - ``build_datalab_cause_agent`` — factory the source registry wires for DATALAB
    when ``DATALAB_LLM_ENABLED`` is on. Degrades to the plain rule path
    (``RuleSourceAgent``) when no Gemini key/client is available, so a missing key
    never changes behaviour vs. LLM-off.

Importing this package pulls in ``langgraph``; the registry imports it lazily
(inside the factory) so the rule-only path never requires langgraph.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from os import getenv
from typing import Any

from app.agents.datalab.agent import DataLabAnalysisAgent
from app.agents.datalab.graph import DataLabAnalysisGraphAgent
from app.agents.datalab.llm_classifier import DataLabCauseClassifier
from app.analyzers.config import DataLabRuleConfig
from app.analyzers.datalab import DataLabAnalyzer
from app.core.config import get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "DataLabAnalysisAgent",
    "DataLabAnalysisGraphAgent",
    "DataLabCauseClassifier",
    "build_datalab_cause_agent",
    "datalab_llm_enabled",
]


def datalab_llm_enabled() -> bool:
    """Whether the DataLab cause agent should run (opt-in env flag)."""
    return str(getenv("DATALAB_LLM_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}


def build_datalab_cause_agent(
    connection: Any,
    *,
    config: DataLabRuleConfig | None = None,
    client: Any | None = None,
    price_provider: Any | None = None,
):
    """Build the DataLab cause agent, or degrade to the rule path.

    Returns a ``DataLabAnalysisGraphAgent`` when an LLM client can be built;
    otherwise a transparent ``RuleSourceAgent`` so a missing key/transport leaves
    DataLab behaving exactly as the LLM-off rule path (no cause tag, same score).
    """
    config = config or DataLabRuleConfig.from_env()
    analyzer = DataLabAnalyzer(config)

    client = client or _try_build_gemini()
    if client is None:
        from app.agents.rule_source_agent import RuleSourceAgent

        return RuleSourceAgent(analyzer)

    provider = price_provider or _market_price_provider(connection, lookback_days=config.lookback_days)
    return DataLabAnalysisGraphAgent(
        analyzer=analyzer,
        classifier=DataLabCauseClassifier(client),
        price_provider=provider,
        lookback_days=config.lookback_days,
    )


def _try_build_gemini() -> Any | None:
    if not get_settings().gemini_api_key:
        return None
    try:
        from app.clients.gemini_client import GeminiJsonClient

        return GeminiJsonClient(api_key=get_settings().gemini_api_key)
    except Exception as exc:  # noqa: BLE001 — missing key/transport: degrade, don't stall
        # 조용히 삼키면 cause 에이전트가 왜 규칙 경로로 떨어졌는지 알 수 없다(hiring
        # 팩토리와 동일하게 경고만 남기고 규칙 경로로 폴백).
        logger.warning("DataLab cause LLM 클라이언트 미구성 (%s) — 규칙 경로로 폴백", exc)
        return None


def _market_price_provider(connection: Any, *, lookback_days: int):
    """Async (stock_id, as_of) -> [{"trade_date", "close"}] over the lookback window."""
    from signal_alpha_data_access.repositories.market_data import MarketDataRepository

    repository = MarketDataRepository(connection)

    async def provider(stock_id: int, as_of: Any) -> list[dict]:
        start = as_of - timedelta(days=lookback_days)
        rows = await repository.list_ohlcv_between(
            stock_id=stock_id, start_date=start, end_date=as_of
        )
        return [{"trade_date": row["trade_date"], "close": row["close"]} for row in rows]

    return provider
