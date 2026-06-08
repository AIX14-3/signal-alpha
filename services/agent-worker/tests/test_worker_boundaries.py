import unittest

from app.analyzers.base import Analyzer
from app.collectors.base import Collector
from app.orchestrator.pipeline import AgentOrchestrator, SourcePipeline
from app.schemas.evidence import RawEvidence
from app.schemas.source_result import EvidenceItem, SourceResult


class StaticCollector:
    source = "dart"

    def collect(self, stock_code: str) -> list[RawEvidence]:
        return [
            RawEvidence(
                source="dart",
                stock_code=stock_code,
                title="분기보고서 접수",
                content="매출과 고부가 제품 비중 관련 원문 일부",
                published_at="2026-05-15",
                url="https://dart.example/report"
            )
        ]


class StaticAnalyzer:
    source = "dart"

    def analyze(self, stock_code: str, evidence: list[RawEvidence]) -> SourceResult:
        first = evidence[0]
        return SourceResult(
            source="dart",
            stock_code=stock_code,
            direction="positive",
            score=72.0,
            summary="공시 원문에서 긍정 방향의 정보 변화가 확인됩니다.",
            evidence_items=[
                EvidenceItem(
                    title=first.title,
                    summary="매출과 제품 믹스 개선을 근거로 요약했습니다.",
                    url=first.url,
                    published_at=first.published_at
                )
            ],
            risk_flags=[]
        )


class WorkerBoundaryTest(unittest.TestCase):
    def test_collector_returns_raw_evidence_without_analysis_fields(self) -> None:
        collector: Collector = StaticCollector()

        evidence = collector.collect("005930")

        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].source, "dart")
        self.assertFalse(hasattr(evidence[0], "direction"))
        self.assertFalse(hasattr(evidence[0], "score"))
        self.assertFalse(hasattr(evidence[0], "summary"))

    def test_analyzer_turns_raw_evidence_into_source_result(self) -> None:
        collector: Collector = StaticCollector()
        analyzer: Analyzer = StaticAnalyzer()

        result = analyzer.analyze("005930", collector.collect("005930"))

        self.assertEqual(result.source, "dart")
        self.assertEqual(result.stock_code, "005930")
        self.assertEqual(result.direction, "positive")
        self.assertEqual(result.score, 72.0)
        self.assertIn("긍정 방향", result.summary)

    def test_orchestrator_connects_collector_to_analyzer(self) -> None:
        pipeline = SourcePipeline(
            source="dart",
            collector=StaticCollector(),
            analyzer=StaticAnalyzer()
        )

        result = pipeline.run("005930")

        self.assertEqual(result.source, "dart")
        self.assertEqual(result.evidence_items[0].title, "분기보고서 접수")

    def test_agent_orchestrator_runs_source_pipelines_by_stock_code(self) -> None:
        orchestrator = AgentOrchestrator(
            pipelines=[
                SourcePipeline(
                    source="dart",
                    collector=StaticCollector(),
                    analyzer=StaticAnalyzer()
                )
            ]
        )

        results = orchestrator.run("005930")

        self.assertEqual(list(results.keys()), ["dart"])
        self.assertEqual(results["dart"].stock_code, "005930")
        self.assertEqual(results["dart"].direction, "positive")


if __name__ == "__main__":
    unittest.main()
