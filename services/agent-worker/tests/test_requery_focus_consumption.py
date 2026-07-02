"""Wave-3 되묻기 실동작화: the re-query focus (#738 wired to
``SourceAgentInput.context["requery_focus"]``) is *consumed* by the three
re-queryable source agents' opt-in LLM classify path.

Covers, for HIRING / PATENT / DATALAB:
  - focus present → the focus hint is threaded into the LLM prompt (and to the
    classifier as ``requery_focus``);
  - focus absent (plain analyze) → the prompt is byte-identical to pre-focus;
  - focus consumption never moves the numbers (score/direction);
  - degrade: a malformed focus / rule-only path is byte-identical (no crash);
plus unit tests for the shared ``requery_focus`` helper.
"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.agents.base import SourceAgentInput
from app.agents.datalab.agent import DataLabAnalysisAgent
from app.agents.datalab.llm_classifier import CauseVerdict
from app.agents.datalab.llm_classifier import _build_prompt as datalab_build_prompt
from app.agents.hiring import HiringAnalysisAgent, HiringSkillClassifier
from app.agents.hiring.llm_classifier import FocusVerdict
from app.agents.hiring.llm_classifier import _build_prompt as hiring_build_prompt
from app.agents.patent.agent import PatentSignificanceAgent
from app.agents.patent.llm_classifier import MaterialityVerdict
from app.agents.patent.llm_classifier import _build_prompt as patent_build_prompt
from app.agents.requery_focus import (
    focus_hint_from_context,
    requery_focus_prompt_block,
)
from app.agents.datalab.lead_lag import LeadLag
from app.schemas.evidence import RawEvidence

AS_OF = date(2026, 6, 1)


# Structured per-source focus slice as ``requery._focus_for_source`` emits it.
def _structured_focus(source: str, *, ask: str) -> dict:
    return {
        "reasons": ["mixed_direction", "low_source_agreement"],
        "headline_signal": "positive",
        "direction": "negative",
        "diverges_from_headline": True,
        "needs_review": True,
        "risk_flags": ["stale_data"],
        "ask": ask,
        "source": source,
    }


HIRING_ASK = "HIRING 재검토 요청: 방향(negative)이 종합 헤드라인(positive)과 어긋나는지 근거를 다시 확인해 주세요."
PATENT_ASK = "PATENT 재검토 요청: 방향(negative)이 종합 헤드라인(positive)과 어긋나는지 근거를 다시 확인해 주세요."
DATALAB_ASK = "DATALAB 재검토 요청: 방향(negative)이 종합 헤드라인(positive)과 어긋나는지 근거를 다시 확인해 주세요."


class FakeClassifier:
    """Captures ``classify`` kwargs so a test can assert the threaded focus."""

    model = "fake-gemini"

    def __init__(self, verdict):
        self._verdict = verdict
        self.calls: list[dict] = []

    async def classify(self, **kwargs):
        self.calls.append(kwargs)
        return self._verdict


# ---------------------------------------------------------------------------
# shared helper
# ---------------------------------------------------------------------------
class RequeryFocusHelperTest(unittest.TestCase):
    def test_structured_focus_prefers_ask(self):
        ctx = {"requery_focus": _structured_focus("HIRING", ask=HIRING_ASK)}
        self.assertEqual(focus_hint_from_context(ctx), HIRING_ASK)

    def test_mapping_without_ask_composes_axes(self):
        ctx = {
            "requery_focus": {
                "direction": "negative",
                "headline_signal": "positive",
                "diverges_from_headline": True,
                "needs_review": True,
                "risk_flags": ["stale_data"],
            }
        }
        hint = focus_hint_from_context(ctx)
        self.assertIsNotNone(hint)
        self.assertIn("negative", hint)
        self.assertIn("positive", hint)
        self.assertIn("needs_review", hint)
        self.assertIn("stale_data", hint)

    def test_legacy_list_focus(self):
        ctx = {"requery_focus": ["mixed_direction", "low_source_agreement"]}
        hint = focus_hint_from_context(ctx)
        self.assertIn("mixed_direction", hint)
        self.assertIn("low_source_agreement", hint)

    def test_string_focus(self):
        self.assertEqual(focus_hint_from_context({"requery_focus": " 재확인 "}), "재확인")

    def test_no_focus_and_degrade(self):
        self.assertIsNone(focus_hint_from_context(None))
        self.assertIsNone(focus_hint_from_context({}))
        self.assertIsNone(focus_hint_from_context({"requery_focus": None}))
        self.assertIsNone(focus_hint_from_context({"requery_focus": {}}))
        self.assertIsNone(focus_hint_from_context({"requery_focus": 12345}))
        # A non-mapping context must not raise.
        self.assertIsNone(focus_hint_from_context("not a context"))

    def test_prompt_block_empty_without_hint(self):
        self.assertEqual(requery_focus_prompt_block(None), "")
        self.assertEqual(requery_focus_prompt_block(""), "")

    def test_prompt_block_carries_hint(self):
        block = requery_focus_prompt_block("살펴봐")
        self.assertIn("오케스트레이터 재질의", block)
        self.assertIn("살펴봐", block)


# ---------------------------------------------------------------------------
# prompt builders: byte-identical without focus, carry hint with focus
# ---------------------------------------------------------------------------
class PromptInjectionTest(unittest.TestCase):
    def test_datalab_prompt(self):
        ll = LeadLag(
            search_recent_avg=200.0, search_prior_avg=100.0,
            search_momentum_pct=0.5, price_prior_return=0.0, price_recent_return=0.1,
            preliminary_cause="catalyst", price_points=10, note="테스트",
        )
        kw = dict(stock_code="005930", rule_direction="positive", rule_score=0.4,
                  lead_lag=ll, summary="요약")
        base = datalab_build_prompt(**kw)
        self.assertEqual(datalab_build_prompt(**kw, requery_focus=None), base)
        withf = datalab_build_prompt(**kw, requery_focus=DATALAB_ASK)
        self.assertNotEqual(withf, base)
        self.assertIn(DATALAB_ASK, withf)

    def test_patent_prompt(self):
        kw = dict(stock_code="005930", rule_direction="positive", rule_score=0.4,
                  summary="요약", filings=[], prelabel="strategic")
        base = patent_build_prompt(**kw)
        self.assertEqual(patent_build_prompt(**kw, requery_focus=None), base)
        withf = patent_build_prompt(**kw, requery_focus=PATENT_ASK)
        self.assertNotEqual(withf, base)
        self.assertIn(PATENT_ASK, withf)

    def test_hiring_prompt(self):
        kw = dict(stock_code="005930", top_functions=["데이터 엔지니어"],
                  top_skills=["Python"], momentum_pct=0.2)
        base = hiring_build_prompt(**kw)
        self.assertEqual(hiring_build_prompt(**kw, requery_focus=None), base)
        withf = hiring_build_prompt(**kw, requery_focus=HIRING_ASK)
        self.assertNotEqual(withf, base)
        self.assertIn(HIRING_ASK, withf)


# ---------------------------------------------------------------------------
# HIRING agent
# ---------------------------------------------------------------------------
def _hiring_rows():
    def _row(day, title, skills):
        return {
            "observed_date": day, "job_count": 6, "seasonal_factor": 1.0,
            "change_pct": None, "job_title": title, "tech_stack": [], "ocr_skills": skills,
        }
    return [
        _row("2026-05-10", "데이터 엔지니어", ["Python", "Kubernetes"]),
        _row("2026-05-20", "ML 엔지니어", ["PyTorch"]),
    ]


def _hiring_input(*, context=None):
    return SourceAgentInput(
        source="HIRING", stock_code="005930", stock_id=1, analysis_date=AS_OF,
        evidence=[RawEvidence(source="HIRING", stock_code="005930", title="채용",
                              content="", metadata={"rows": _hiring_rows(),
                                                     "as_of": AS_OF.isoformat(), "lookback_days": 90})],
        context=context or {},
    )


class HiringConsumeTest(unittest.IsolatedAsyncioTestCase):
    def _agent(self, fake):
        return HiringAnalysisAgent(classifier=cast(HiringSkillClassifier, fake))

    async def test_focus_threaded_to_classifier(self):
        fake = FakeClassifier(FocusVerdict(focus="AI", rationale="AI 역량"))
        ctx = {"requery_focus": _structured_focus("HIRING", ask=HIRING_ASK)}
        await self._agent(fake).analyze(_hiring_input(context=ctx))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["requery_focus"], HIRING_ASK)

    async def test_plain_analyze_passes_none(self):
        fake = FakeClassifier(FocusVerdict(focus="AI", rationale="AI 역량"))
        await self._agent(fake).analyze(_hiring_input())
        self.assertIsNone(fake.calls[0]["requery_focus"])

    async def test_numbers_unchanged_by_focus(self):
        v = FocusVerdict(focus="AI", rationale="AI 역량")
        ctx = {"requery_focus": _structured_focus("HIRING", ask=HIRING_ASK)}
        withf = await self._agent(FakeClassifier(v)).analyze(_hiring_input(context=ctx))
        without = await self._agent(FakeClassifier(v)).analyze(_hiring_input())
        self.assertEqual(withf.score, without.score)
        self.assertEqual(withf.direction, without.direction)

    async def test_malformed_focus_degrades(self):
        fake = FakeClassifier(FocusVerdict(focus="AI", rationale="AI 역량"))
        await self._agent(fake).analyze(_hiring_input(context={"requery_focus": 999}))
        self.assertIsNone(fake.calls[0]["requery_focus"])


# ---------------------------------------------------------------------------
# PATENT agent
# ---------------------------------------------------------------------------
def _patent_rows():
    def _row(day, tech, new=False, sig=None):
        return {
            "application_no": f"10-2026-{day.replace('-', '')}", "patent_title": "특허",
            "applicant_name": "삼성", "application_date": day, "tech_category": tech,
            "is_new_category": new, "source_url": None, "llm_features": None, "significance": sig,
        }
    return [
        _row("2026-05-25", "H01", new=True, sig=0.85), _row("2026-05-20", "G06", sig=0.8),
        _row("2026-04-15", "H01", new=True, sig=0.78), _row("2026-03-10", "G06", sig=0.75),
        _row("2026-02-05", "G06", sig=0.7), _row("2026-01-20", "G06", sig=0.72),
        _row("2025-04-01", "G06"), _row("2025-03-01", "G06"),
    ]


def _patent_input(*, context=None):
    return SourceAgentInput(
        source="PATENT", stock_code="005930", stock_id=1, analysis_date=AS_OF,
        evidence=[RawEvidence(source="PATENT", stock_code="005930", title="특허",
                              content="", metadata={"rows": _patent_rows(),
                                                     "as_of": AS_OF.isoformat(), "lookback_days": 365})],
        context=context or {},
    )


class PatentConsumeTest(unittest.IsolatedAsyncioTestCase):
    def _agent(self, fake):
        return PatentSignificanceAgent(classifier=fake)

    async def test_focus_threaded_to_classifier(self):
        fake = FakeClassifier(MaterialityVerdict("strategic", "근거", 0.8))
        ctx = {"requery_focus": _structured_focus("PATENT", ask=PATENT_ASK)}
        await self._agent(fake).analyze(_patent_input(context=ctx))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["requery_focus"], PATENT_ASK)

    async def test_plain_analyze_passes_none(self):
        fake = FakeClassifier(MaterialityVerdict("strategic", "근거", 0.8))
        await self._agent(fake).analyze(_patent_input())
        self.assertIsNone(fake.calls[0]["requery_focus"])

    async def test_numbers_unchanged_by_focus(self):
        v = MaterialityVerdict("strategic", "근거", 0.8)
        ctx = {"requery_focus": _structured_focus("PATENT", ask=PATENT_ASK)}
        withf = await self._agent(FakeClassifier(v)).analyze(_patent_input(context=ctx))
        without = await self._agent(FakeClassifier(v)).analyze(_patent_input())
        self.assertEqual(withf.score, without.score)
        self.assertEqual(withf.direction, without.direction)

    async def test_malformed_focus_degrades(self):
        fake = FakeClassifier(MaterialityVerdict("strategic", "근거", 0.8))
        await self._agent(fake).analyze(_patent_input(context={"requery_focus": object()}))
        self.assertIsNone(fake.calls[0]["requery_focus"])


# ---------------------------------------------------------------------------
# DATALAB agent
# ---------------------------------------------------------------------------
def _spike_series(as_of=AS_OF, n_prior=30):
    series = {}
    start = as_of - timedelta(days=n_prior)
    for i in range(n_prior):
        series[(start + timedelta(days=i)).isoformat()] = 98.0 if i % 2 == 0 else 102.0
    series[as_of.isoformat()] = 140.0
    return series


def _datalab_rows():
    def _row(day, index, spike=False, change=None):
        return {"observed_date": day, "search_index": index, "is_spike": spike,
                "weight": 1.0, "polarity": "demand", "change_pct": change}
    return [
        _row("2026-05-05", 100), _row("2026-05-08", 105), _row("2026-05-12", 110),
        _row("2026-05-20", 180, spike=True, change=60), _row("2026-05-26", 200, spike=True, change=70),
        _row("2026-05-30", 210, change=5),
    ]


async def _datalab_price(stock_id, as_of):
    return [
        {"trade_date": "2026-05-05", "close": 100.0},
        {"trade_date": "2026-05-15", "close": 100.0},
        {"trade_date": "2026-05-30", "close": 112.0},
    ]


def _datalab_input(*, context=None):
    return SourceAgentInput(
        source="DATALAB", stock_code="005930", stock_id=1, analysis_date=AS_OF,
        evidence=[RawEvidence(source="DATALAB", stock_code="005930", title="검색",
                              content="", metadata={"rows": _datalab_rows(),
                                                     "as_of": AS_OF.isoformat(), "lookback_days": 30,
                                                     "attention_series": _spike_series()})],
        context=context or {},
    )


class DataLabConsumeTest(unittest.IsolatedAsyncioTestCase):
    def _agent(self, fake):
        return DataLabAnalysisAgent(classifier=fake, price_provider=_datalab_price, lookback_days=30)

    async def test_focus_threaded_to_classifier(self):
        fake = FakeClassifier(CauseVerdict("catalyst", "근거", 0.8))
        ctx = {"requery_focus": _structured_focus("DATALAB", ask=DATALAB_ASK)}
        await self._agent(fake).analyze(_datalab_input(context=ctx))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["requery_focus"], DATALAB_ASK)

    async def test_plain_analyze_passes_none(self):
        fake = FakeClassifier(CauseVerdict("catalyst", "근거", 0.8))
        await self._agent(fake).analyze(_datalab_input())
        self.assertIsNone(fake.calls[0]["requery_focus"])

    async def test_numbers_unchanged_by_focus(self):
        ctx = {"requery_focus": _structured_focus("DATALAB", ask=DATALAB_ASK)}
        withf = await self._agent(FakeClassifier(CauseVerdict("catalyst", "근거", 0.8))).analyze(
            _datalab_input(context=ctx))
        without = await self._agent(FakeClassifier(CauseVerdict("catalyst", "근거", 0.8))).analyze(
            _datalab_input())
        self.assertEqual(withf.score, without.score)
        self.assertEqual(withf.direction, without.direction)

    async def test_malformed_focus_degrades(self):
        fake = FakeClassifier(CauseVerdict("catalyst", "근거", 0.8))
        await self._agent(fake).analyze(_datalab_input(context={"requery_focus": 3.14}))
        self.assertIsNone(fake.calls[0]["requery_focus"])


if __name__ == "__main__":
    unittest.main()
