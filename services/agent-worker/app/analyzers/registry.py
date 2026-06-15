"""Source registry for the Alternative harness.

One entry per registered source ties together its pure analyzer, the loader that
feeds it from the DB, the aggregation weight (via config), and the ``debate_method``
code it persists under. The AlternativeAgent iterates this list — adding a source
(e.g. HIRING, by a teammate) is a single ``SourceRegistration`` append, no agent
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


@dataclass(frozen=True)
class SourceRegistration:
    source: str
    debate_method: str
    analyzer: Analyzer
    loader_factory: Callable[[Any], EvidenceLoader]

    def build_loader(self, repository: Any) -> EvidenceLoader:
        return self.loader_factory(repository)


def build_registry(
    *,
    hiring_config: HiringRuleConfig | None = None,
    patent_config: PatentRuleConfig | None = None,
    datalab_config: DataLabRuleConfig | None = None,
) -> list[SourceRegistration]:
    hiring_config = hiring_config or HiringRuleConfig.from_env()
    patent_config = patent_config or PatentRuleConfig.from_env()
    datalab_config = datalab_config or DataLabRuleConfig.from_env()

    return [
        SourceRegistration(
            source="HIRING",
            debate_method=DEBATE_METHODS["HIRING"],
            analyzer=HiringAnalyzer(hiring_config),
            loader_factory=lambda repo, cfg=hiring_config: HiringEvidenceLoader(
                repo, lookback_days=cfg.lookback_days
            ),
        ),
        SourceRegistration(
            source="PATENT",
            debate_method=DEBATE_METHODS["PATENT"],
            analyzer=PatentAnalyzer(patent_config),
            loader_factory=lambda repo, cfg=patent_config: PatentEvidenceLoader(
                repo, lookback_days=cfg.lookback_days
            ),
        ),
        SourceRegistration(
            source="DATALAB",
            debate_method=DEBATE_METHODS["DATALAB"],
            analyzer=DataLabAnalyzer(datalab_config),
            loader_factory=lambda repo, cfg=datalab_config: DataLabEvidenceLoader(
                repo, lookback_days=cfg.lookback_days
            ),
        ),
    ]
