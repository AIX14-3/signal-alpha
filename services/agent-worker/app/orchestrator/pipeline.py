from dataclasses import dataclass

from app.analyzers.base import Analyzer
from app.collectors.base import Collector
from app.schemas.evidence import SourceType
from app.schemas.source_result import SourceResult


@dataclass(frozen=True)
class SourcePipeline:
    source: SourceType
    collector: Collector
    analyzer: Analyzer

    def run(self, stock_code: str) -> SourceResult:
        evidence = self.collector.collect(stock_code)
        return self.analyzer.analyze(stock_code, evidence)


@dataclass(frozen=True)
class AgentOrchestrator:
    pipelines: list[SourcePipeline]

    def run(self, stock_code: str) -> dict[SourceType, SourceResult]:
        return {
            pipeline.source: pipeline.run(stock_code)
            for pipeline in self.pipelines
        }
