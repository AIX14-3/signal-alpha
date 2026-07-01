import unittest
from datetime import date

from app.agents import SourceAgentInput
from app.agents.dart.agent import DartAnalysisAgent


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


class DartAnalysisAgentTest(unittest.IsolatedAsyncioTestCase):
    """Phase 0(#546): 피처 전용 — 판정/LLM 경로 제거."""

    async def test_agent_returns_features_only(self):
        agent = DartAnalysisAgent()

        result = await agent.analyze(
            SourceAgentInput(source="DART", stock_code="005930", events=[periodic_report_event()])
        )

        self.assertEqual(result.source, "DART")
        self.assertEqual(result.stock_code, "005930")
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.data_status, "no_signal")
        self.assertEqual(result.analysis_source, "features")
        self.assertEqual(result.prompt_ver, "dart-features-v1")
        self.assertIsNone(result.llm_model)
        self.assertIsNone(result.llm_error)
        self.assertEqual(result.method_detail["source"], "DART")
        self.assertEqual(result.method_detail["event_count"], 1)
