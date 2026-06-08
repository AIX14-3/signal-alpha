import unittest

from app.analyzers.base import Analyzer
from app.collectors.base import Collector
from app.orchestrator.pipeline import AgentOrchestrator, SourcePipeline
from app.schemas.evidence import RawEvidence
from app.schemas.source_result import EvidenceItem, SourceResult


class StaticCollector:
    source = "DART"

    async def collect(self, stock_code: str) -> list[RawEvidence]:
        return [
            RawEvidence(
                source="DART",
                stock_code=stock_code,
                title="Quarterly report accepted",
                content="Revenue mix and product demand changed.",
                published_at="2026-05-15",
                url="https://dart.example/report",
            )
        ]


class StaticAnalyzer:
    source = "DART"

    async def analyze(self, stock_code: str, evidence: list[RawEvidence]) -> SourceResult:
        first = evidence[0]
        return SourceResult(
            source="DART",
            stock_code=stock_code,
            direction="positive",
            score=72.0,
            summary="Official filing evidence shows a positive direction.",
            evidence_items=[
                EvidenceItem(
                    title=first.title,
                    summary="The filing was summarized as positive evidence.",
                    url=first.url,
                    published_at=first.published_at,
                )
            ],
            risk_flags=[],
        )


class WorkerBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_collector_returns_raw_evidence_without_analysis_fields(self) -> None:
        collector: Collector = StaticCollector()

        evidence = await collector.collect("005930")

        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].source, "DART")
        self.assertFalse(hasattr(evidence[0], "direction"))
        self.assertFalse(hasattr(evidence[0], "score"))
        self.assertFalse(hasattr(evidence[0], "summary"))

    async def test_analyzer_turns_raw_evidence_into_source_result(self) -> None:
        collector: Collector = StaticCollector()
        analyzer: Analyzer = StaticAnalyzer()

        result = await analyzer.analyze("005930", await collector.collect("005930"))

        self.assertEqual(result.source, "DART")
        self.assertEqual(result.stock_code, "005930")
        self.assertEqual(result.direction, "positive")
        self.assertEqual(result.score, 72.0)
        self.assertIn("positive direction", result.summary)

    async def test_orchestrator_connects_collector_to_analyzer(self) -> None:
        pipeline = SourcePipeline(
            source="DART",
            collector=StaticCollector(),
            analyzer=StaticAnalyzer(),
        )

        result = await pipeline.run("005930")

        self.assertEqual(result.source, "DART")
        self.assertEqual(result.evidence_items[0].title, "Quarterly report accepted")

    async def test_agent_orchestrator_runs_source_pipelines_by_stock_code(self) -> None:
        orchestrator = AgentOrchestrator(
            pipelines=[
                SourcePipeline(
                    source="DART",
                    collector=StaticCollector(),
                    analyzer=StaticAnalyzer(),
                )
            ]
        )

        results = await orchestrator.run("005930")

        self.assertEqual(list(results.keys()), ["DART"])
        self.assertEqual(results["DART"].stock_code, "005930")
        self.assertEqual(results["DART"].direction, "positive")


if __name__ == "__main__":
    unittest.main()
