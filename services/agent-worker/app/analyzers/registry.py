"""Source registry for the Alternative harness.

One entry per registered source ties together its pure analyzer, the loader that
feeds it from the DB, the aggregation weight (via config), and the ``debate_method``
code it persists under. The analyze handler iterates this list — adding a source
(e.g. HIRING, by a teammate) is a single ``SourceRegistration`` append, no handler
or aggregator change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from os import getenv
from typing import Any, Callable

from app.analyzers.base import Analyzer
from app.analyzers.config import DataLabRuleConfig, HiringRuleConfig, PatentRuleConfig
from app.analyzers.datalab import DataLabAnalyzer
from app.analyzers.hiring import HiringAnalyzer
from app.analyzers.patent import PatentAnalyzer
from app.evidence_loaders import (
    DataLabEvidenceLoader,
    HiringEvidenceLoader,
    PatentEvidenceLoader,
)
from app.evidence_loaders.base import EvidenceLoader

# debate_method is a schema-level enum (agent_results CHECK D-1..D-5); pin each
# source to a distinct code in one place. A new source picks an unused code.
DEBATE_METHODS = {
    "HIRING": "D-1",
    "PATENT": "D-2",
    "DATALAB": "D-3",
}

# Each source now publishes its OWN final_signals row, so it needs its own
# run_key — the (stock_id, signal_date, run_key) tuple is what keeps the three
# sources' "current" signals from colliding (uq_final_signal_current, 001_baseline).
# Kept distinct from DART's run_key (read filters use ``run_key LIKE 'DART%'``).
RUN_KEYS = {
    "HIRING": "HIRING",
    "PATENT": "PATENT",
    "DATALAB": "DATALAB",
}

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def _env_enabled(name: str) -> bool:
    return (getenv(name) or "").strip().lower() in _TRUTHY


def _hiring_agent_factory(
    config: HiringRuleConfig,
) -> Callable[[Any], Any] | None:
    """Tier-B HIRING agent factory, gated by ``HIRING_LLM_ENABLED`` (default off).

    Off → returns None so the handler drives the pure analyzer through
    ``RuleSourceAgent`` (byte-identical to the current path). On → returns a
    per-task factory that builds ``HiringAnalysisAgent`` with a single-shot Gemini
    skill/duty-focus classifier. A missing GEMINI_API_KEY degrades the agent to
    the deterministic focus summary (classifier=None), never stalling analysis.
    The score/direction stay owned by the rule analyzer on every path.
    """
    if not _env_enabled("HIRING_LLM_ENABLED"):
        return None

    def factory(_connection: Any) -> Any:
        from app.agents.hiring import HiringAnalysisAgent, HiringSkillClassifier

        return HiringAnalysisAgent(config=config, classifier=_build_hiring_classifier(HiringSkillClassifier))

    return factory


def _build_hiring_classifier(classifier_cls: type) -> Any | None:
    """A Gemini-backed focus classifier, or None when the key/transport is absent."""
    try:
        from app.clients.gemini_client import GeminiJsonClient
        from app.core.config import get_settings

        return classifier_cls(GeminiJsonClient(api_key=get_settings().gemini_api_key))
    except Exception as exc:  # noqa: BLE001 — missing key/deps: degrade, don't stall
        logger.warning("HIRING focus classifier 미구성 (%s) — 결정론 포커스로 폴백", exc)
        return None


@dataclass(frozen=True)
class SourceRegistration:
    source: str
    debate_method: str
    analyzer: Analyzer
    loader_factory: Callable[[Any], EvidenceLoader]
    # final_signals/analysis_results run_key for this source. Defaults to None so
    # callers constructing a registration ad hoc still work; resolve via
    # ``resolved_run_key`` which falls back to the source name.
    run_key: str | None = None
    # Optional graph/LLM agent for this source, built per-task from the DB
    # connection. When None the handler drives the pure analyzer through
    # ``RuleSourceAgent`` (current path) — so leaving it unset is byte-identical to
    # before. HIRING sets it (gated by ``HIRING_LLM_ENABLED``, default off) for the
    # Tier-B skill/duty-focus agent; the score/direction still come from the rule
    # analyzer, the agent only attaches focus evidence.
    agent_factory: Callable[[Any], Any] | None = None

    def build_loader(self, repository: Any) -> EvidenceLoader:
        return self.loader_factory(repository)

    @property
    def resolved_run_key(self) -> str:
        return self.run_key or RUN_KEYS.get(self.source, self.source)


def build_registry(
    *,
    hiring_config: HiringRuleConfig | None = None,
    patent_config: PatentRuleConfig | None = None,
    datalab_config: DataLabRuleConfig | None = None,
) -> list[SourceRegistration]:
    hiring_config = hiring_config or HiringRuleConfig.from_env()
    patent_config = patent_config or PatentRuleConfig.from_env()
    datalab_config = datalab_config or DataLabRuleConfig.from_env()

    # DataLab 생성형 cause agent는 판정 경로에서 영구 비활성(결정론 전처리 원칙 —
    # docs/archive/design/worker-redesign.md). lead_lag 결정론 prelabel이 최종 라벨이고, 검색
    # 시계열은 통계지표(indicators/rules)로만 피처화한다. cause agent 모듈
    # (app/agents/datalab/*)은 보존하되 와이어링하지 않으므로 agent_factory를 두지 않는다.

    # Patent significance agent (기획 §2.1) is opt-in. The env is checked inline (no
    # import) and the agent package is imported lazily inside the factory, so the
    # rule-only path never imports it. Off → agent_factory stays None → the handler
    # drives PatentAnalyzer through RuleSourceAgent (byte-identical to before).
    patent_agent_factory: Callable[[Any], Any] | None = None
    if _patent_llm_enabled():
        def patent_agent_factory(connection: Any, cfg: PatentRuleConfig = patent_config) -> Any:
            from app.agents.patent import build_patent_agent

            return build_patent_agent(connection, config=cfg)

    return [
        SourceRegistration(
            source="HIRING",
            debate_method=DEBATE_METHODS["HIRING"],
            run_key=RUN_KEYS["HIRING"],
            analyzer=HiringAnalyzer(hiring_config),
            loader_factory=lambda repo, cfg=hiring_config: HiringEvidenceLoader(
                repo, lookback_days=cfg.lookback_days
            ),
            agent_factory=_hiring_agent_factory(hiring_config),
        ),
        SourceRegistration(
            source="PATENT",
            debate_method=DEBATE_METHODS["PATENT"],
            run_key=RUN_KEYS["PATENT"],
            analyzer=PatentAnalyzer(patent_config),
            loader_factory=lambda repo, cfg=patent_config: PatentEvidenceLoader(
                repo, lookback_days=cfg.lookback_days
            ),
            agent_factory=patent_agent_factory,
        ),
        SourceRegistration(
            source="DATALAB",
            debate_method=DEBATE_METHODS["DATALAB"],
            run_key=RUN_KEYS["DATALAB"],
            analyzer=DataLabAnalyzer(datalab_config),
            loader_factory=lambda repo, cfg=datalab_config: DataLabEvidenceLoader(
                repo,
                lookback_days=cfg.lookback_days,
                attention_window_days=cfg.attention_window_days,
            ),
        ),
    ]


def registration_for(
    source: str,
    *,
    hiring_config: HiringRuleConfig | None = None,
    patent_config: PatentRuleConfig | None = None,
    datalab_config: DataLabRuleConfig | None = None,
) -> SourceRegistration:
    """The single ``SourceRegistration`` for one source.

    Each ANALYZE_{SOURCE} stage handler analyzes exactly one source, so it is
    constructed with a one-element registration list. This pulls that source out
    of ``build_registry`` (the full list stays the source of truth — adding a
    source here is still a single append there). Raises if the source is unknown.
    """
    registry = build_registry(
        hiring_config=hiring_config,
        patent_config=patent_config,
        datalab_config=datalab_config,
    )
    try:
        return next(r for r in registry if r.source == source)
    except StopIteration:
        raise KeyError(f"unknown alternative source: {source!r}") from None


def _patent_llm_enabled() -> bool:
    """Patent significance agent opt-in flag. Inline (no heavy import) so checking
    it never pulls in the agent package."""
    return str(getenv("PATENT_LLM_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
