import unittest
from typing import cast

from app.agents import RuleSourceAgent, SourceAgentInput, SourceAnalysisAgent
from app.analyzers.hiring import HiringAnalyzer
from app.schemas.evidence import RawEvidence
from app.schemas.source_result import EvidenceItem, ReportMeta, SourceResult


class _StubAnalyzer:
    """Minimal Analyzer that returns a preset SourceResult, ignoring inputs."""

    def __init__(self, result: SourceResult) -> None:
        self.source = result.source
        self._result = result

    async def analyze(self, stock_code, evidence) -> SourceResult:  # noqa: ANN001
        return self._result


class RuleSourceAgentTest(unittest.IsolatedAsyncioTestCase):
    def test_wraps_analyzer_as_source_agent_contract(self):
        agent = RuleSourceAgent(_StubAnalyzer(_ok_result()))
        self.assertIsInstance(agent, SourceAnalysisAgent)
        self.assertEqual(agent.source, "PATENT")
        self.assertEqual(agent.prompt_ver, "patent-rules-v1")

    async def test_maps_source_result_fields_through_unchanged(self):
        result = _ok_result()
        agent = RuleSourceAgent(_StubAnalyzer(result))

        output = await cast(SourceAnalysisAgent, agent).analyze(
            SourceAgentInput(source="PATENT", stock_code="005930")
        )

        self.assertEqual(output.source, "PATENT")
        self.assertEqual(output.stock_code, "005930")
        self.assertEqual(output.direction, "positive")
        self.assertEqual(output.score, result.score)
        self.assertEqual(output.summary, result.summary)
        self.assertEqual(output.risk_flags, ["watch"])
        self.assertEqual(output.data_status, "ok")
        self.assertFalse(output.needs_review)
        self.assertEqual(output.analysis_source, "rules")
        self.assertIsNone(output.llm_model)
        self.assertEqual(output.prompt_ver, "patent-rules-v1")
        self.assertIsNone(output.llm_error)
        # evidence_items / report_meta land in method_detail as plain JSON.
        self.assertEqual(output.method_detail["data_status"], "ok")
        self.assertEqual(len(output.method_detail["evidence_items"]), 1)
        self.assertEqual(output.method_detail["evidence_items"][0]["title"], "filing")
        self.assertEqual(output.method_detail["report_meta"]["target_trend"], "up")

    async def test_partial_status_flags_needs_review(self):
        result = _ok_result(data_status="partial")
        agent = RuleSourceAgent(_StubAnalyzer(result))

        output = await agent.analyze(SourceAgentInput(source="PATENT", stock_code="005930"))

        self.assertEqual(output.data_status, "partial")
        self.assertTrue(output.needs_review)

    async def test_llm_provenance_passes_through_without_changing_source(self):
        # DataLab polarity attaches an llm_model; analysis stays rule-based.
        result = _ok_result(llm_model="gemini-2.5")
        agent = RuleSourceAgent(_StubAnalyzer(result))

        output = await agent.analyze(SourceAgentInput(source="PATENT", stock_code="005930"))

        self.assertEqual(output.llm_model, "gemini-2.5")
        self.assertEqual(output.analysis_source, "rules")

    def test_prompt_ver_override(self):
        agent = RuleSourceAgent(_StubAnalyzer(_ok_result()), prompt_ver="patent-rules-v2")
        self.assertEqual(agent.prompt_ver, "patent-rules-v2")

    async def test_real_hiring_analyzer_through_contract(self):
        agent = RuleSourceAgent(HiringAnalyzer())
        self.assertEqual(agent.source, "HIRING")

        # Empty evidence → analyzer reports failed/unknown; mapping must surface it.
        output = await agent.analyze(
            SourceAgentInput(
                source="HIRING",
                stock_code="005930",
                evidence=[
                    RawEvidence(
                        source="HIRING",
                        stock_code="005930",
                        title="no rows",
                        content="",
                        metadata={"rows": []},
                    )
                ],
            )
        )

        self.assertEqual(output.source, "HIRING")
        self.assertEqual(output.direction, "unknown")
        self.assertEqual(output.data_status, "failed")
        self.assertTrue(output.needs_review)
        self.assertEqual(output.analysis_source, "rules")
        self.assertEqual(output.prompt_ver, "hiring-rules-v1")


def _ok_result(*, data_status="ok", llm_model=None) -> SourceResult:
    return SourceResult(
        source="PATENT",
        stock_code="005930",
        direction="positive",
        score=0.42,
        summary="patent activity rising",
        evidence_items=[EvidenceItem(title="filing", summary="a new filing")],
        risk_flags=["watch"],
        data_status=data_status,
        report_meta=ReportMeta(
            avg_target=None,
            upside_pct=None,
            target_trend="up",
            conflict_detected=False,
        ),
        llm_model=llm_model,
    )


if __name__ == "__main__":
    unittest.main()
