"""Hiring Tier-B agent: focus gate, LLM success/fallback, and the score-invariant
round-trip through the orchestrator seam.

채용 verdict 복원(2026-07) 후 규칙 ``HiringAnalyzer`` 는 실제 점수/방향을 낸다. 따라서 핵심
불변식은 "score==0.0" 이 아니라 **LLM 포커스 보강이 규칙이 소유한 score/direction 을 움직이지
않고 focus *근거*만 붙인다**는 것이다(각 테스트는 규칙-only 기준선과 비교한다).
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


async def _rules_baseline(rows):
    """규칙 분석기(LLM classifier 없음)가 내는 (score, direction) — LLM 불변식 비교 기준선.

    채용 verdict 복원 후 규칙이 실제 점수를 내므로, LLM 경로가 이 값을 그대로 보존하는지로
    "LLM 은 점수를 안 움직인다" 불변식을 검증한다(옛 0.0 하드코딩 대체)."""
    out = await HiringAnalysisAgent(config=CONFIG).analyze(_input(rows))
    return out.score, out.direction


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
        # 채용 verdict 복원 후 규칙이 실제 점수를 낸다(LLM 없이도 결정론 산출) — 값은 존재.
        self.assertIsInstance(output.score, float)

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
        base_score, base_dir = await _rules_baseline(_focus_rows())
        fake = FakeClassifier()
        agent = HiringAnalysisAgent(config=CONFIG, classifier=cast(HiringSkillClassifier, fake))
        output = await agent.analyze(_input(_focus_rows()))

        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(output.analysis_source, "llm")
        self.assertEqual(output.llm_model, "fake-gemini")
        self.assertEqual(output.prompt_ver, PROMPT_VERSION)
        # ★ score/direction owned by the rule analyzer — never moved by the LLM.
        self.assertEqual(output.score, base_score)
        self.assertEqual(output.direction, base_dir)
        self.assertIsNone(output.llm_error)
        # Focus surfaces in the summary and as a round-trippable evidence item.
        self.assertIn("채용 포커스", output.summary)
        self.assertIn("AI·백엔드", output.summary)
        titles = [e["title"] for e in output.method_detail["evidence_items"]]
        self.assertIn("채용 전략 포커스", titles)
        self.assertEqual(output.method_detail["hiring_focus"]["source"], "llm")
        self.assertTrue(output.method_detail["hiring_focus"]["top_functions"])

    async def test_llm_failure_degrades_to_deterministic_focus(self):
        base_score, base_dir = await _rules_baseline(_focus_rows())
        fake = FakeClassifier(fail=True)
        agent = HiringAnalysisAgent(config=CONFIG, classifier=cast(HiringSkillClassifier, fake))
        output = await agent.analyze(_input(_focus_rows()))

        self.assertEqual(output.analysis_source, "rules_fallback")
        self.assertIsNone(output.llm_model)
        self.assertIsNotNone(output.llm_error)
        self.assertEqual(output.score, base_score)  # LLM 실패해도 규칙 점수 불변
        self.assertEqual(output.direction, base_dir)
        # Deterministic focus summary still present (skills / functions listed).
        self.assertIn("채용 포커스", output.summary)
        self.assertIn("요구 기술", output.summary)

    async def test_empty_llm_rationale_degrades_with_rules_provenance(self):
        # LLM 이 근거 문장을 못 내면 결정론 포커스 요약이 저장된다 — 그 값의 출처는
        # 규칙이므로 provenance 도 rules_fallback 이어야 한다("llm" 오라벨 금지).
        fake = FakeClassifier(verdict=FocusVerdict(focus=None, rationale=""))
        agent = HiringAnalysisAgent(config=CONFIG, classifier=cast(HiringSkillClassifier, fake))
        output = await agent.analyze(_input(_focus_rows()))

        self.assertEqual(len(fake.calls), 1)  # LLM 은 호출됐지만 값을 못 냈다
        self.assertEqual(output.analysis_source, "rules_fallback")
        self.assertEqual(output.method_detail["hiring_focus"]["source"], "rules_fallback")
        self.assertIsNone(output.llm_model)
        self.assertIsNone(output.llm_error)  # 실패 아님 — 근거 미제공일 뿐
        # 결정론 포커스 요약은 그대로 실린다.
        self.assertIn("요구 기술", output.summary)

    async def test_focus_output_round_trips_score_invariant(self):
        # The orchestrator restores a SourceResult from the agent output; score and
        # direction must survive the round-trip unchanged (Alternative invariance).
        base_score, base_dir = await _rules_baseline(_focus_rows())
        fake = FakeClassifier()
        agent = HiringAnalysisAgent(config=CONFIG, classifier=cast(HiringSkillClassifier, fake))
        output = await agent.analyze(_input(_focus_rows()))
        restored = _from_output(output)
        self.assertEqual(restored.score, base_score)  # 라운드트립에도 규칙 점수 불변
        self.assertEqual(restored.direction, base_dir)
        self.assertEqual(restored.source, "HIRING")
        # The focus evidence item survives into the restored SourceResult.
        self.assertTrue(any(e.title == "채용 전략 포커스" for e in restored.evidence_items))


class HiringFocusMetadataScanTest(unittest.TestCase):
    """_build_focus/_as_of 는 rows 를 찾은 것과 같은 전 항목 스캔으로 메타데이터를
    읽는다 — evidence[0] 고정이면 rows 가 두 번째 항목에 실릴 때 as_of/sector_demand
    가 유실됐다."""

    def _two_item_input(self):
        # midpoint(=AS_OF-45d) 이전 prior 행 1건 + recent 행 3건 → momentum 이
        # AS_OF 기준으로 결정론 계산된다: ((4+6+8)/3 - 2)/2 = 2.0.
        rows = [_row("2026-03-01", job_count=2, title="백엔드 개발자", skills=["Go"])] + _focus_rows()
        return SourceAgentInput(
            source="HIRING",
            stock_code="005930",
            stock_id=1,
            analysis_date=None,  # 메타데이터 as_of 폴백 경로를 태운다
            evidence=[
                RawEvidence(
                    source="HIRING", stock_code="005930", title="빈 항목", content="",
                    metadata={},  # rows/as_of/sector_demand 없음
                ),
                RawEvidence(
                    source="HIRING", stock_code="005930", title="채용 공고", content="",
                    metadata={
                        "rows": rows,
                        "as_of": AS_OF.isoformat(),
                        "lookback_days": 90,
                        "sector_demand": {"momentum_pct": 0.1, "coverage_weight": 0.5},
                    },
                ),
            ],
        )

    def test_metadata_read_from_item_carrying_rows(self):
        from app.agents.hiring.agent import _as_of

        input_data = self._two_item_input()
        # as_of 는 두 번째 항목의 메타데이터에서 나와야 한다(오늘 날짜 폴백 아님).
        self.assertEqual(_as_of(input_data), AS_OF)

        agent = HiringAnalysisAgent(config=CONFIG)
        focus = agent._build_focus(input_data)
        self.assertTrue(focus.top_skills)  # rows 는 이미 전 항목 스캔으로 찾았다
        # AS_OF 기준 midpoint 분할이 적용된 결정론 값 — evidence[0] 고정(date.today()
        # 폴백)이었다면 실행 시점에 따라 다른 값/None 이 된다.
        self.assertEqual(focus.momentum_pct, 2.0)


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
