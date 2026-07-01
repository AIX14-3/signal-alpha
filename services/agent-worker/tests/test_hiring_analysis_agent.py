"""Hiring Tier-B agent: focus gate, LLM success/fallback, and the score-invariant
round-trip through the orchestrator seam.

The rule ``HiringAnalyzer`` is Phase 0 (direction="unknown", score=0.0), so the
central assertion on every path is that the agent leaves score/direction untouched
and only attaches focus *evidence*.
"""

import sys
import unittest
from datetime import date
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.agents import SourceAgentInput, SourceAnalysisAgent
from app.agents.hiring import HiringAnalysisAgent, HiringSkillClassifier
from app.agents.hiring.llm_classifier import PROMPT_VERSION, FocusVerdict
from app.analyzers.config import HiringRuleConfig
from app.analyzers.registry import build_registry
from app.orchestrator.alternative.tasks import _from_output
from app.schemas.evidence import RawEvidence

AS_OF = date(2026, 6, 1)
CONFIG = HiringRuleConfig()  # defaults; lookback_days=90


def _row(day: str, *, job_count: int, title: str, skills=None):
    return {
        "observed_date": day,
        "job_count": job_count,
        "seasonal_factor": 1.0,
        "change_pct": None,
        "job_title": title,
        "tech_stack": [],
        "ocr_skills": list(skills or []),
    }


def _evidence(rows, *, as_of=AS_OF):
    return [
        RawEvidence(
            source="HIRING",
            stock_code="005930",
            title="채용 공고",
            content="",
            published_at=rows[-1]["observed_date"] if rows else None,
            metadata={"rows": rows, "as_of": as_of.isoformat(), "lookback_days": 90},
        )
    ]


def _focus_rows():
    # AI/engineer titles + OCR skills → deterministic focus is non-empty (gate opens).
    return [
        _row("2026-05-10", job_count=4, title="데이터 엔지니어", skills=["Python", "Kubernetes"]),
        _row("2026-05-20", job_count=6, title="ML 엔지니어", skills=["PyTorch", "Docker"]),
        _row("2026-05-28", job_count=8, title="백엔드 개발자", skills=["Go", "Redis"]),
    ]


def _input(rows):
    return SourceAgentInput(
        source="HIRING",
        stock_code="005930",
        stock_id=1,
        analysis_date=AS_OF,
        evidence=_evidence(rows),
    )


class FakeClassifier:
    model = "fake-gemini"

    def __init__(self, *, verdict=None, fail=False):
        self._verdict = verdict or FocusVerdict(focus="데이터/AI 인재", rationale="AI·백엔드 역량에 채용을 집중하고 있다.")
        self._fail = fail
        self.calls = []

    async def classify(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail:
            raise RuntimeError("LLM down")
        return self._verdict


class HiringAgentGateTest(unittest.IsolatedAsyncioTestCase):
    def test_satisfies_source_agent_contract(self):
        agent = HiringAnalysisAgent(config=CONFIG)
        self.assertIsInstance(agent, SourceAnalysisAgent)
        self.assertEqual(agent.source, "HIRING")

    async def test_no_classifier_skips_llm_and_stays_rules(self):
        agent = HiringAnalysisAgent(config=CONFIG)  # classifier=None
        output = await agent.analyze(_input(_focus_rows()))
        self.assertEqual(output.analysis_source, "rules")
        self.assertIsNone(output.llm_model)
        self.assertEqual(output.score, 0.0)
        self.assertEqual(output.direction, "unknown")

    async def test_empty_focus_skips_llm(self):
        # Rows with no classifiable title and no skills → focus empty → gate closed.
        fake = FakeClassifier()
        agent = HiringAnalysisAgent(config=CONFIG, classifier=cast(HiringSkillClassifier, fake))
        rows = [_row("2026-05-20", job_count=5, title="상담 접수", skills=[])]
        output = await agent.analyze(_input(rows))
        self.assertEqual(fake.calls, [])
        self.assertEqual(output.analysis_source, "rules")

    async def test_no_rows_skips_llm(self):
        fake = FakeClassifier()
        agent = HiringAnalysisAgent(config=CONFIG, classifier=cast(HiringSkillClassifier, fake))
        output = await agent.analyze(
            SourceAgentInput(
                source="HIRING",
                stock_code="005930",
                evidence=[RawEvidence(source="HIRING", stock_code="005930", title="x", content="", metadata={"rows": []})],
            )
        )
        self.assertEqual(fake.calls, [])
        self.assertEqual(output.data_status, "no_signal")
        self.assertEqual(output.analysis_source, "rules")


class HiringAgentFocusTest(unittest.IsolatedAsyncioTestCase):
    async def test_llm_success_attaches_focus_without_moving_score(self):
        fake = FakeClassifier()
        agent = HiringAnalysisAgent(config=CONFIG, classifier=cast(HiringSkillClassifier, fake))
        output = await agent.analyze(_input(_focus_rows()))

        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(output.analysis_source, "llm")
        self.assertEqual(output.llm_model, "fake-gemini")
        self.assertEqual(output.prompt_ver, PROMPT_VERSION)
        # ★ score/direction owned by the rule analyzer — never moved by the LLM.
        self.assertEqual(output.score, 0.0)
        self.assertEqual(output.direction, "unknown")
        self.assertIsNone(output.llm_error)
        # Focus surfaces in the summary and as a round-trippable evidence item.
        self.assertIn("채용 포커스", output.summary)
        self.assertIn("AI·백엔드", output.summary)
        titles = [e["title"] for e in output.method_detail["evidence_items"]]
        self.assertIn("채용 전략 포커스", titles)
        self.assertEqual(output.method_detail["hiring_focus"]["source"], "llm")
        self.assertTrue(output.method_detail["hiring_focus"]["top_functions"])

    async def test_llm_failure_degrades_to_deterministic_focus(self):
        fake = FakeClassifier(fail=True)
        agent = HiringAnalysisAgent(config=CONFIG, classifier=cast(HiringSkillClassifier, fake))
        output = await agent.analyze(_input(_focus_rows()))

        self.assertEqual(output.analysis_source, "rules_fallback")
        self.assertIsNone(output.llm_model)
        self.assertIsNotNone(output.llm_error)
        self.assertEqual(output.score, 0.0)
        self.assertEqual(output.direction, "unknown")
        # Deterministic focus summary still present (skills / functions listed).
        self.assertIn("채용 포커스", output.summary)
        self.assertIn("요구 기술", output.summary)

    async def test_focus_output_round_trips_score_invariant(self):
        # The orchestrator restores a SourceResult from the agent output; score and
        # direction must survive the round-trip unchanged (Alternative invariance).
        fake = FakeClassifier()
        agent = HiringAnalysisAgent(config=CONFIG, classifier=cast(HiringSkillClassifier, fake))
        output = await agent.analyze(_input(_focus_rows()))
        restored = _from_output(output)
        self.assertEqual(restored.score, 0.0)
        self.assertEqual(restored.direction, "unknown")
        self.assertEqual(restored.source, "HIRING")
        # The focus evidence item survives into the restored SourceResult.
        self.assertTrue(any(e.title == "채용 전략 포커스" for e in restored.evidence_items))


class HiringRegistryWiringTest(unittest.TestCase):
    def _hiring_reg(self):
        return next(r for r in build_registry() if r.source == "HIRING")

    def test_flag_off_leaves_agent_factory_none(self):
        import os

        os.environ.pop("HIRING_LLM_ENABLED", None)
        self.assertIsNone(self._hiring_reg().agent_factory)

    def test_flag_on_sets_agent_factory(self):
        import os

        os.environ["HIRING_LLM_ENABLED"] = "true"
        try:
            factory = self._hiring_reg().agent_factory
            self.assertIsNotNone(factory)
            # The factory builds a HiringAnalysisAgent even with no API key
            # (classifier degrades to None; analysis still runs on rules).
            agent = factory(None)
            self.assertIsInstance(agent, HiringAnalysisAgent)
        finally:
            os.environ.pop("HIRING_LLM_ENABLED", None)


if __name__ == "__main__":
    unittest.main()
