import unittest
from datetime import date
from typing import cast

from app.agents import SourceAgentInput, SourceAgentOutput, SourceAnalysisAgent
from app.agents.dart.agent import DartAnalysisAgent
from app.schemas.evidence import RawEvidence


class SourceAgentContractTest(unittest.IsolatedAsyncioTestCase):
    def test_source_agent_input_defaults_optional_collections(self):
        input_data = SourceAgentInput(source="DART", stock_code="005930")

        self.assertEqual(input_data.stock_id, None)
        self.assertEqual(input_data.analysis_date, None)
        self.assertEqual(input_data.run_key, None)
        self.assertEqual(input_data.events, [])
        self.assertEqual(input_data.evidence, [])
        self.assertEqual(input_data.context, {})

    def test_source_agent_output_defaults_operational_metadata(self):
        output = SourceAgentOutput(
            source="DART",
            stock_code="005930",
            direction="neutral",
            score=0.0,
            summary="DART disclosures are neutral.",
        )

        self.assertEqual(output.risk_flags, [])
        self.assertEqual(output.method_detail, {})
        self.assertFalse(output.needs_review)
        self.assertEqual(output.data_status, "ok")
        self.assertEqual(output.analysis_source, "rules")
        self.assertIsNone(output.llm_model)
        self.assertEqual(output.prompt_ver, "1.0")
        self.assertIsNone(output.llm_error)

    async def test_dart_agent_can_be_called_through_source_agent_contract(self):
        agent = DartAnalysisAgent()
        self.assertIsInstance(agent, SourceAnalysisAgent)

        result = await cast(SourceAnalysisAgent, agent).analyze(
            SourceAgentInput(
                source="DART",
                stock_code="005930",
                stock_id=1,
                analysis_date=date(2026, 6, 15),
                run_key="DART_EVENT_501",
                events=[
                    {
                        "id": 501,
                        "event_type": "quarter_report",
                        "event_date": date(2026, 5, 15),
                        "signal_direction": "neutral",
                        "impact_level": "medium",
                        "title": "Quarterly report",
                        "summary": "DART disclosure: Quarterly report",
                        "evidence_text": "Revenue improved.",
                        "needs_review": False,
                    }
                ],
                evidence=[
                    RawEvidence(
                        source="DART",
                        stock_code="005930",
                        title="Quarterly report",
                        content="Revenue improved.",
                    )
                ],
                context={"source_type": "DART"},
            )
        )

        self.assertEqual(result.source, "DART")
        self.assertEqual(result.stock_code, "005930")
        self.assertEqual(result.analysis_source, "rules")
        self.assertEqual(result.prompt_ver, "dart-rules-v1")
