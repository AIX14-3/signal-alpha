import unittest
from datetime import date
from typing import cast

from app.agents import SourceAgentInput, SourceAnalysisAgent
from app.agents.dart.graph import DartAnalysisGraphAgent


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

        # Phase 0(#546): 피처 전용 — 판정 없음, AGGREGATE 제외(no_signal).
        self.assertEqual(result.source, "DART")
        self.assertEqual(result.stock_code, "005930")
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.data_status, "no_signal")
        self.assertEqual(result.analysis_source, "features")
        self.assertEqual(result.prompt_ver, "dart-features-v1")
        self.assertEqual(result.method_detail["graph"], "dart_analysis_v1")
        self.assertEqual(result.method_detail["graph_nodes"], ["validate_input", "analyze", "validate_output"])

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
