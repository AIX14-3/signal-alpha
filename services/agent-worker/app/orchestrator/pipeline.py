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

    async def run(self, stock_code: str) -> SourceResult:
        evidence = await self.collector.collect(stock_code)
        return await self.analyzer.analyze(stock_code, evidence)


@dataclass(frozen=True)
class AgentOrchestrator:
    pipelines: list[SourcePipeline]

    async def run(self, stock_code: str) -> dict[SourceType, SourceResult]:
        results: dict[SourceType, SourceResult] = {}
        for pipeline in self.pipelines:
            results[pipeline.source] = await pipeline.run(stock_code)
        return results
