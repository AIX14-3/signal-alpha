"""Source registry for the Alternative harness.

One entry per registered source ties together its pure analyzer, the loader that
feeds it from the DB, the aggregation weight (via config), and the ``debate_method``
code it persists under. The analyze handler iterates this list — adding a source
(e.g. HIRING, by a teammate) is a single ``SourceRegistration`` append, no handler
or aggregator change.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    # connection (e.g. the DataLab cause agent needs price access). When None the
    # handler drives the pure analyzer through ``RuleSourceAgent`` (current path) —
    # so leaving it unset is byte-identical to before. Only DATALAB sets it, and
    # only when ``DATALAB_LLM_ENABLED`` is on.
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
    return [
        SourceRegistration(
            source="HIRING",
            debate_method=DEBATE_METHODS["HIRING"],
            run_key=RUN_KEYS["HIRING"],
            analyzer=HiringAnalyzer(hiring_config),
            loader_factory=lambda repo, cfg=hiring_config: HiringEvidenceLoader(
                repo, lookback_days=cfg.lookback_days
            ),
        ),
        SourceRegistration(
            source="PATENT",
            debate_method=DEBATE_METHODS["PATENT"],
            run_key=RUN_KEYS["PATENT"],
            analyzer=PatentAnalyzer(patent_config),
            loader_factory=lambda repo, cfg=patent_config: PatentEvidenceLoader(
                repo, lookback_days=cfg.lookback_days
            ),
        ),
        SourceRegistration(
            source="DATALAB",
            debate_method=DEBATE_METHODS["DATALAB"],
            run_key=RUN_KEYS["DATALAB"],
            analyzer=DataLabAnalyzer(datalab_config),
            loader_factory=lambda repo, cfg=datalab_config: DataLabEvidenceLoader(
                repo, lookback_days=cfg.lookback_days
            ),
        ),
    ]
