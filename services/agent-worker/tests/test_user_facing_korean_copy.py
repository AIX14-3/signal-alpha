"""사용자에게 그대로 노출되는 문장의 회귀 방지.

LLM 서술이 없으면(미구성/실패) 아래 문장들이 **그대로 화면에 나간다**. 실제로 리포트
화면에 파이썬 dict repr(``{'neutral': 1}``), 영어 템플릿("… data shows a mixed data
direction."), 같은 문장 15번 반복이 노출된 적이 있다(사용자 신고).
"""

from __future__ import annotations

import re
import unittest

from app.analyzers.dart.source_result import build_dart_analysis_result
from app.orchestrator.aggregation.tasks import NormalizedSourceResult, _blend_group, _summary

# 파이썬 컨테이너 repr 이 문장에 새어 나왔는지 판별. {'correction': 1} / ['a', 'b'] 등.
_PY_REPR = re.compile(r"[{\[]\s*'")
_ASCII_SENTENCE = re.compile(r"[A-Za-z]{4,}\s+[A-Za-z]{4,}")


def _dart_event(event_type: str, direction: str = "neutral", needs_review: bool = False) -> dict:
    return {
        "id": 1,
        "event_type": event_type,
        "signal_direction": direction,
        "impact_level": "medium",
        "title": "제목",
        "summary": "요약",
        "event_date": "2026-07-09",
        "needs_review": needs_review,
        "is_official": True,
    }


def _result(source: str, summary: str) -> NormalizedSourceResult:
    return NormalizedSourceResult(
        source=source,
        analysis_result_id=1,
        agent_result_id=1,
        direction="neutral",
        score=0.0,
        score_100=50.0,
        data_status="no_signal",
        needs_review=False,
        risk_flags=[],
        summary=summary,
        source_signal_event_ids=[],
        valuation=None,
    )


class DartSummaryCopyTest(unittest.TestCase):
    def test_summary_never_leaks_python_dict_repr(self):
        result = build_dart_analysis_result(
            [_dart_event("dart_disclosure"), _dart_event("insider_ownership")]
        )

        self.assertNotRegex(result.summary, _PY_REPR)
        self.assertNotIn("dart_disclosure", result.summary)
        self.assertNotIn("insider_ownership", result.summary)

    def test_summary_names_disclosure_types_in_korean(self):
        result = build_dart_analysis_result([_dart_event("correction"), _dart_event("correction")])

        self.assertIn("정정공시 2건", result.summary)
        self.assertIn("최근 공시 2건", result.summary)


class BlendedSummaryTest(unittest.TestCase):
    def test_identical_summaries_are_not_repeated(self):
        # DART 는 공시 1건당 런이 하나씩 생겨 같은 템플릿이 열댓 번 반복된 채 이어붙었다.
        same = "최근 공시 1건을 확인했습니다(일반공시 1건)."
        group = [_result("DART", same) for _ in range(15)]

        blended = _blend_group("DART", group)

        self.assertEqual(blended.summary, same)
        self.assertEqual(blended.summary.count("최근 공시"), 1)

    def test_distinct_summaries_are_capped(self):
        group = [_result("DART", f"문장 {i}") for i in range(10)]

        blended = _blend_group("DART", group)

        self.assertEqual(blended.summary.count(" / "), 2)  # 3문장 상한


class AggregateSummaryCopyTest(unittest.TestCase):
    def test_summary_is_korean_not_english(self):
        summary = _summary(
            "mixed",
            [_result("REPORT", "요약")],
            ["DART", "PRICE"],
            "CAUTION",
        )

        self.assertNotRegex(summary, _ASCII_SENTENCE)
        self.assertIn("증권사 리포트", summary)
        self.assertIn("엇갈리는", summary)
        # 미수집 소스도 한국어 이름으로.
        self.assertIn("공시", summary)
        self.assertIn("주가", summary)

    def test_summary_without_sources_is_korean(self):
        self.assertNotRegex(_summary("neutral", [], [], "NORMAL"), _ASCII_SENTENCE)


if __name__ == "__main__":
    unittest.main()
