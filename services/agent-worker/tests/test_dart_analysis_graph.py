import unittest
from datetime import date
from typing import cast

from app.agents import SourceAgentInput, SourceAnalysisAgent
from app.agents.dart.graph import DartAnalysisGraphAgent
from app.analyzers.dart.llm import DartLlmAnalysis


class FakeLlmAnalyzer:
    model = "test-llm"
    prompt_version = "dart-llm-v1"

    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    async def analyze(self, *, events, rule_result, stock_code):
        self.calls.append({"events": events, "rule_result": rule_result, "stock_code": stock_code})
        if self.fail:
            raise RuntimeError("LLM timeout")
        return DartLlmAnalysis(
            direction="positive",
            score=0.73,
            summary="LLM reviewed the disclosure and found improving performance.",
            key_facts=["Revenue improved", "Operating profit improved"],
            risk_flags=[],
            needs_review=False,
            confidence=82,
        )


def periodic_report_event():
    return {
        "id": 501,
        "event_type": "quarter_report",
        "event_date": date(2026, 5, 15),
        "signal_direction": "neutral",
        "impact_level": "medium",
        "title": "Quarterly report",
        "summary": "DART disclosure: Quarterly report",
        "evidence_text": "Revenue improved.",
        "evidence_url": "https://dart.example/receipt",
        "needs_review": False,
        "is_official": True,
    }


class DartAnalysisGraphAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_graph_agent_can_be_called_through_source_agent_contract(self):
        agent = DartAnalysisGraphAgent()
        self.assertIsInstance(agent, SourceAnalysisAgent)

        result = await cast(SourceAnalysisAgent, agent).analyze(
            SourceAgentInput(source="DART", stock_code="005930", events=[periodic_report_event()])
        )

        self.assertEqual(result.source, "DART")
        self.assertEqual(result.stock_code, "005930")
        self.assertEqual(result.direction, "neutral")
        self.assertEqual(result.analysis_source, "rules")
        self.assertEqual(result.prompt_ver, "dart-rules-v1")
        self.assertEqual(result.method_detail["graph"], "dart_analysis_v1")
        self.assertEqual(result.method_detail["graph_nodes"], ["validate_input", "analyze", "validate_output"])

    async def test_graph_agent_runs_llm_path_and_keeps_output_contract(self):
        llm_analyzer = FakeLlmAnalyzer()
        agent = DartAnalysisGraphAgent(llm_analyzer=llm_analyzer)

        result = await agent.analyze(
            SourceAgentInput(source="DART", stock_code="005930", events=[periodic_report_event()])
        )

        self.assertEqual(result.direction, "positive")
        self.assertEqual(result.score, 0.73)
        self.assertEqual(result.analysis_source, "llm")
        self.assertEqual(result.llm_model, "test-llm")
        self.assertEqual(result.prompt_ver, "dart-llm-v1")
        self.assertEqual(result.method_detail["graph"], "dart_analysis_v1")
        self.assertEqual(len(llm_analyzer.calls), 1)

    async def test_graph_agent_marks_invalid_input_as_failed_without_persisting_fake_analysis(self):
        agent = DartAnalysisGraphAgent()

        result = await agent.analyze(SourceAgentInput(source="DART", stock_code="", events=[]))

        self.assertEqual(result.source, "DART")
        self.assertEqual(result.stock_code, "")
        self.assertEqual(result.direction, "neutral")
        self.assertEqual(result.score, 0)
        self.assertTrue(result.needs_review)
        self.assertEqual(result.data_status, "failed")
        self.assertEqual(result.analysis_source, "graph_validation")
        self.assertEqual(result.prompt_ver, "dart-graph-v1")
        self.assertIn("stock_code_required", result.risk_flags)
