"""Patent significance agent (기획 §2.1): significance gate, deterministic
prelabel, LLM materiality tag, LLM-failure fallback, score-invariance, and the
summary round-trip through the orchestrator seam."""

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.agents.base import SourceAgentInput
from app.agents.patent import build_patent_agent
from app.agents.patent.agent import PatentSignificanceAgent, _deterministic_prelabel, _top_filings
from app.agents.patent.llm_classifier import PROMPT_VERSION, MaterialityVerdict
from app.agents.rule_source_agent import RuleSourceAgent
from app.orchestrator.alternative.tasks import _from_output
from app.schemas.evidence import RawEvidence

AS_OF = date(2026, 6, 1)
LOOKBACK = 365  # evidence 메타의 명목값(분석기는 실제로 config 기본값을 사용).
# 분석기는 PatentRuleConfig.from_env() 기본 lookback_days(=900, 공개지연 stopgap)를
# 쓰고 위 메타값은 무시한다. 900일 창의 midpoint = AS_OF - 450d ≈ 2025-03-08:
# 이벤트일(공개일 폴백 출원일)이 그 이후면 "recent", 이전이면 "prior".


def _patent_row(day, *, tech="G06", new=False, significance=None, title="특허"):
    return {
        "application_no": f"10-2026-{day.replace('-', '')}",
        "patent_title": title,
        "applicant_name": "삼성전자",
        "application_date": day,
        "tech_category": tech,
        "is_new_category": new,
        "source_url": None,
        "llm_features": None,
        "significance": significance,
    }


def _evidence(rows, *, as_of=AS_OF):
    return [
        RawEvidence(
            source="PATENT",
            stock_code="005930",
            title="특허 출원",
            content="",
            published_at=rows[0]["application_date"] if rows else None,
            metadata={"rows": rows, "as_of": as_of.isoformat(), "lookback_days": LOOKBACK},
        )
    ]


def _notable_rows():
    # 6 recent (2026) vs 2 prior (early 2025): strong positive momentum + new tech
    # categories + high per-patent significance → rule score well past the gate.
    return [
        _patent_row("2026-05-25", tech="H01", new=True, significance=0.85, title="차세대 배터리 셀"),
        _patent_row("2026-05-20", tech="G06", significance=0.80, title="온디바이스 AI 가속기"),
        _patent_row("2026-04-15", tech="H01", new=True, significance=0.78, title="전고체 전해질"),
        _patent_row("2026-03-10", tech="G06", significance=0.75),
        _patent_row("2026-02-05", tech="G06", significance=0.70),
        _patent_row("2026-01-20", tech="G06", significance=0.72),
        _patent_row("2025-04-01", tech="G06"),
        _patent_row("2025-03-01", tech="G06"),
    ]


def _weak_rows():
    # 900일 창(midpoint ≈ 2025-03-08) 기준 2 recent(이후) vs 2 prior(이전·창 내) →
    # 평탄 momentum + 신규분류 없음 + significance 없음 → activity-only 점수가 게이트
    # (0.2) 아래, direction neutral. (옛 365일 전제 데이터는 900일 창에서 4건이 모두
    # recent 가 되어 "활동 개시"로 게이트를 넘겼다.)
    return [
        _patent_row("2026-05-01"),
        _patent_row("2026-04-01"),
        _patent_row("2024-06-01"),
        _patent_row("2024-05-01"),
    ]


class FakeClassifier:
    model = "fake-gemini"

    def __init__(self, *, verdict=None, fail=False):
        self._verdict = verdict
        self._fail = fail
        self.calls = []

    async def classify(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail:
            raise RuntimeError("LLM down")
        return self._verdict


def _input(rows):
    return SourceAgentInput(
        source="PATENT", stock_code="005930", stock_id=1,
        analysis_date=AS_OF, evidence=_evidence(rows),
    )


class PatentAgentGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_notable_signal_runs_llm_and_tags_materiality(self):
        classifier = FakeClassifier(
            verdict=MaterialityVerdict(materiality="strategic", rationale="신규 배터리 분류 확장", confidence=0.8)
        )
        agent = PatentSignificanceAgent(classifier=classifier)
        out = await agent.analyze(_input(_notable_rows()))

        self.assertEqual(out.analysis_source, "llm")
        self.assertEqual(out.llm_model, "fake-gemini")
        self.assertEqual(out.method_detail["patent_materiality"], "strategic")
        self.assertEqual(out.method_detail["materiality_source"], "llm")
        self.assertIn("[특허 유의성: strategic]", out.summary)
        self.assertIn("신규 배터리 분류 확장", out.summary)
        self.assertEqual(len(classifier.calls), 1)

    async def test_weak_signal_skips_classifier(self):
        classifier = FakeClassifier(verdict=MaterialityVerdict("strategic", "x", 0.5))
        agent = PatentSignificanceAgent(classifier=classifier)
        out = await agent.analyze(_input(_weak_rows()))

        self.assertEqual(out.analysis_source, "rules")
        self.assertNotIn("patent_materiality", out.method_detail)
        self.assertEqual(len(classifier.calls), 0)

    async def test_no_data_skips_classifier(self):
        classifier = FakeClassifier(verdict=MaterialityVerdict("strategic", "x", 0.5))
        agent = PatentSignificanceAgent(classifier=classifier)
        out = await agent.analyze(_input([]))

        self.assertEqual(out.direction, "unknown")
        self.assertEqual(out.analysis_source, "rules")
        self.assertEqual(len(classifier.calls), 0)

    async def test_new_category_gates_even_at_modest_score(self):
        # A lone new-category filing among few: activity score may sit under the
        # threshold, but the new tech category still opens the gate.
        rows = [
            _patent_row("2026-05-01", tech="H01", new=True, title="신규 분야 진입"),
            _patent_row("2026-04-01"),
            _patent_row("2025-06-01"),
        ]
        classifier = FakeClassifier(verdict=MaterialityVerdict("strategic", "신규 분야", 0.6))
        agent = PatentSignificanceAgent(classifier=classifier)
        out = await agent.analyze(_input(rows))
        self.assertEqual(len(classifier.calls), 1)
        self.assertEqual(out.method_detail["patent_materiality"], "strategic")


class PatentAgentFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_llm_failure_falls_back_to_prelabel(self):
        rows = _notable_rows()
        prelabel = _deterministic_prelabel(
            # positive + new category → strategic
            await PatentSignificanceAgent().run_rules(_input(rows)),
            _input(rows),
        )
        self.assertEqual(prelabel, "strategic")

        classifier = FakeClassifier(fail=True)
        agent = PatentSignificanceAgent(classifier=classifier)
        out = await agent.analyze(_input(rows))

        self.assertEqual(out.analysis_source, "rules_fallback")
        self.assertEqual(out.method_detail["patent_materiality"], prelabel)
        self.assertEqual(out.method_detail["materiality_source"], "rules_fallback")
        self.assertIsNotNone(out.llm_error)

    async def test_out_of_enum_verdict_uses_prelabel_with_rules_provenance(self):
        classifier = FakeClassifier(
            verdict=MaterialityVerdict(materiality=None, rationale="형식 오류", confidence=0.0)
        )
        agent = PatentSignificanceAgent(classifier=classifier)
        out = await agent.analyze(_input(_notable_rows()))
        # materiality=None from the parser → agent substitutes the deterministic
        # prelabel, so the provenance must say rules_fallback, NOT "llm" — the LLM
        # never picked the persisted value.
        self.assertEqual(out.analysis_source, "rules_fallback")
        self.assertEqual(out.method_detail["patent_materiality"], "strategic")
        self.assertEqual(out.method_detail["materiality_source"], "rules_fallback")
        self.assertIsNone(out.llm_model)
        self.assertIsNone(out.llm_error)  # 실패 아님 — LLM 이 값을 못 골랐을 뿐

    async def test_ambiguous_is_a_valid_llm_verdict(self):
        # "ambiguous" 는 Materiality enum 의 유효 값 — LLM 이 실제로 고른 판정이므로
        # 그대로 "llm" provenance 로 통과한다(폴백 아님).
        classifier = FakeClassifier(
            verdict=MaterialityVerdict(materiality="ambiguous", rationale="판정 곤란", confidence=0.4)
        )
        agent = PatentSignificanceAgent(classifier=classifier)
        out = await agent.analyze(_input(_notable_rows()))
        self.assertEqual(out.analysis_source, "llm")
        self.assertEqual(out.method_detail["patent_materiality"], "ambiguous")
        self.assertEqual(out.method_detail["materiality_source"], "llm")

    async def test_malformed_payload_parses_to_none_not_prelabel(self):
        # 파서는 형식 오류에서 예비 판정을 주입하지 않는다(오라벨 방지) — 폴백은 에이전트 몫.
        from app.agents.patent.llm_classifier import _parse_verdict

        verdict = _parse_verdict("not-a-dict")
        self.assertIsNone(verdict.materiality)


class PatentAgentInvarianceTest(unittest.IsolatedAsyncioTestCase):
    async def test_score_and_direction_unchanged_by_materiality(self):
        classifier = FakeClassifier(verdict=MaterialityVerdict("strategic", "x", 0.9))
        rows = _notable_rows()
        with_llm = await PatentSignificanceAgent(classifier=classifier).analyze(_input(rows))
        rules_only = await PatentSignificanceAgent().analyze(_input(rows))
        self.assertEqual(with_llm.score, rules_only.score)
        self.assertEqual(with_llm.direction, rules_only.direction)


class PatentAgentFactoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_client_degrades_to_rule_source_agent(self):
        # Force "no Gemini key/client" deterministically — CI sets GEMINI_API_KEY,
        # so patch the builder rather than depend on the ambient environment.
        with patch("app.agents.patent._try_build_gemini", return_value=None):
            agent = build_patent_agent(connection=None, client=None)
        self.assertIsInstance(agent, RuleSourceAgent)
        # And it behaves like the pure rule path — no materiality tag.
        out = await agent.analyze(_input(_notable_rows()))
        self.assertEqual(out.analysis_source, "rules")
        self.assertNotIn("patent_materiality", out.method_detail)

    async def test_client_builds_significance_agent(self):
        agent = build_patent_agent(connection=None, client=FakeClassifier(
            verdict=MaterialityVerdict("sustained", "핵심 R&D 지속", 0.7)
        ))
        self.assertIsInstance(agent, PatentSignificanceAgent)


class PatentAgentRoundTripTest(unittest.IsolatedAsyncioTestCase):
    async def test_from_output_carries_materiality_summary(self):
        classifier = FakeClassifier(
            verdict=MaterialityVerdict("strategic", "신규 분류 확장", 0.8)
        )
        out = await PatentSignificanceAgent(classifier=classifier).analyze(_input(_notable_rows()))
        result = _from_output(out)
        # The orchestrator seam drops non-cause method_detail keys, but the
        # human-facing rationale rides through on `summary` (persisted via _method_detail).
        self.assertIn("[특허 유의성: strategic]", result.summary)
        self.assertIn("신규 분류 확장", result.summary)
        self.assertEqual(result.score, out.score)
        self.assertIsNone(result.cause)  # patent never sets the DataLab-only cause tag


class PatentTopFilingsTest(unittest.TestCase):
    def test_new_category_and_high_significance_first(self):
        top = _top_filings(_input(_notable_rows()))
        self.assertTrue(top[0]["is_new_category"])  # new categories rank first
        self.assertLessEqual(len(top), 8)


class PatentPromptVersionTest(unittest.TestCase):
    def test_fits_agent_results_prompt_ver_column(self):
        # prompt_ver is persisted to agent_results.prompt_ver character varying(20);
        # a longer version string fails the INSERT on PATENT_LLM_ENABLED publishes.
        self.assertLessEqual(len(PROMPT_VERSION), 20)


if __name__ == "__main__":
    unittest.main()
