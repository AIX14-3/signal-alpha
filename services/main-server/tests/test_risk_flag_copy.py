"""위험 플래그가 화면에 영문 식별자로 새어 나가지 않는지 검증.

사용자 리포트: 근거 카드 아래 경고줄에 `search_spike`, `hiring_decline`,
`review_required:correction` 같은 내부 식별자가 그대로 노출됐다. `_RISK_FLAG_KO` 에
키가 없으면 원문을 그대로 내보내던 폴백 때문이다.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.api.routes.reports import _evidence_list, _humanize_summary, _risk_flag_ko

# 분석기 6종이 실제로 내보내는 값 전부(analyzers/*/rules.py 의 risk_flags.append).
_ALL_FLAGS = [
    "no_data",
    "stale_data",
    "insufficient_history",
    "short_history",
    "low_base",
    "missing_source",
    "high_volatility",
    "low_liquidity",
    "volume_spike",
    "overbought",
    "oversold",
    "correction_disclosure",
    "search_spike",
    "risk_search",
    "hiring_decline",
    "no_active_postings",
    "signal_expired",
    "no_valuation_signal",
    "valuation_review_required",
    "implied_multiple_missing",
    "failed_source",
    "analyzer_error",
    "score_out_of_range",
]

_LATIN = re.compile(r"[A-Za-z]")


class RiskFlagCopyTest(unittest.TestCase):
    def test_every_analyzer_flag_maps_to_korean(self):
        for flag in _ALL_FLAGS:
            with self.subTest(flag=flag):
                text = _risk_flag_ko(flag)
                self.assertTrue(text)
                self.assertNotIn(flag, text)
                self.assertNotRegex(text, _LATIN)

    def test_review_required_prefix_is_expanded(self):
        text = _risk_flag_ko("review_required:insider_ownership")

        self.assertIn("임원·주요주주 지분변동", text)
        self.assertNotIn("review_required", text)

    def test_review_required_correction_reads_as_the_correction_notice(self):
        # 두 코드가 같은 사실(정정공시)을 가리킨다 → 같은 문장이어야 중복 제거에서 접힌다.
        self.assertEqual(
            _risk_flag_ko("review_required:correction"), _risk_flag_ko("correction_disclosure")
        )

    def test_unknown_flag_never_leaks_the_identifier(self):
        text = _risk_flag_ko("some_new_flag_nobody_mapped")

        self.assertNotIn("some_new_flag", text)
        self.assertNotRegex(text, _LATIN)

    def test_evidence_list_translates_flags(self):
        items = _evidence_list(
            [{"source": "HIRING", "summary": "요약", "risk_flags": ["hiring_decline"]}]
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "hiring")
        self.assertNotIn("hiring_decline", items[0]["risk_flags"])
        self.assertIn("채용 공고가", items[0]["risk_flags"][0])

    def test_repeated_flags_are_shown_once(self):
        # 분석기가 이벤트마다 같은 플래그를 쌓아 같은 문장이 두 번 뜨던 문제.
        items = _evidence_list(
            [
                {
                    "source": "REPORT",
                    "summary": "요약",
                    "risk_flags": ["valuation_review_required", "valuation_review_required"],
                }
            ]
        )

        self.assertEqual(len(items[0]["risk_flags"]), 1)

    def test_correction_flags_from_two_codes_collapse_to_one_line(self):
        # DART 는 정정공시를 `correction_disclosure` 와 `review_required:correction` 둘 다로 표시한다.
        items = _evidence_list(
            [
                {
                    "source": "DART",
                    "summary": "요약",
                    "risk_flags": ["review_required:correction", "correction_disclosure"],
                }
            ]
        )

        self.assertEqual(len(items[0]["risk_flags"]), 1)


class SummaryHumanizerTest(unittest.TestCase):
    def test_datalab_terse_summary_is_rewritten(self):
        text = _humanize_summary(
            "datalab",
            {
                "summary": "2026-07-06 기준 최근 30일 검색 트렌드 382건 분석: 방향 positive, 점수 +0.569 (모멘텀 +0.211, 스파이크 0.21).",
                "direction": "positive",
            },
        )

        self.assertIn("2026-07-06 기준", text)
        self.assertIn("검색 관심도", text)
        self.assertNotIn("positive", text)
        self.assertNotIn("스파이크", text)

    def test_patent_terse_summary_is_rewritten(self):
        text = _humanize_summary(
            "patent",
            {
                "summary": "2026-02-27 기준 최근 900일 공개 특허 13305건 분석: 방향 mixed, 점수 -0.067.",
                "direction": "mixed",
            },
        )

        self.assertIn("연구개발 흐름", text)
        self.assertNotIn("mixed", text)
        self.assertNotIn("-0.067", text)

    def test_hiring_no_data_summary_hides_the_table_name(self):
        # data_status 가 없는 구버전 발행 행 — 제외 사유는 못 붙이지만 테이블명은 새면 안 된다.
        text = _humanize_summary(
            "hiring", {"summary": "분석할 채용 데이터가 없습니다 (hiring_raw_details 미적재).", "direction": "unknown"}
        )

        self.assertNotIn("hiring_raw_details", text)
        self.assertIn("채용 공고를 찾지 못했습니다", text)

    def test_missing_direction_never_produces_a_broken_sentence(self):
        # direction 이 없으면 예전엔 "방향성 판단 방향으로 읽힙니다" 같은 비문이 나왔다.
        for source, terse in (
            ("datalab", "2026-07-06 기준 최근 30일 검색 트렌드 382건 분석: 방향 positive, 점수 +0.569 (스파이크 0.21)."),
            ("patent", "2026-02-27 기준 최근 900일 공개 특허 13305건 분석: 방향 mixed, 점수 -0.067."),
            ("price", "2026-06-01 기준 방향 positive, 점수 +0.400."),
        ):
            with self.subTest(source=source):
                text = _humanize_summary(source, {"summary": terse, "direction": None})
                self.assertNotIn("방향성 판단 방향", text)
                self.assertNotIn("None", text)

    def test_evidence_list_uses_the_stored_direction(self):
        items = _evidence_list(
            [
                {
                    "source": "PATENT",
                    "direction": "positive",
                    "summary": "2026-02-27 기준 최근 900일 공개 특허 13305건 분석: 방향 positive, 점수 +0.2.",
                    "risk_flags": [],
                }
            ]
        )

        self.assertIn("긍정 방향으로 읽힙니다", items[0]["summary"])

    def test_excluded_source_explains_how_the_score_was_computed(self):
        # "분석할 채용 데이터가 없습니다"로 끝내면 종합 점수가 어디서 왔는지 알 수 없다.
        text = _humanize_summary(
            "hiring",
            {"summary": "분석할 채용 데이터가 없습니다 (hiring_raw_details 미적재).", "data_status": "no_signal"},
        )

        self.assertIn("채용 공고를 찾지 못해", text)
        self.assertIn("나머지 소스로 계산했습니다", text)
        self.assertNotIn("hiring_raw_details", text)

    def test_every_source_has_its_own_exclusion_reason(self):
        for source, fragment in (
            ("dart", "공시에서 방향을 가를"),
            ("price", "시세 데이터가 충분하지"),
            ("report", "증권사 리포트가 없어"),
            ("datalab", "검색량 데이터가 충분하지"),
            ("patent", "공개된 특허를 찾지"),
            ("hiring", "채용 공고를 찾지"),
        ):
            with self.subTest(source=source):
                text = _humanize_summary(source, {"summary": "무엇이든", "data_status": "missing"})
                self.assertIn(fragment, text)
                self.assertIn("나머지 소스로 계산했습니다", text)

    def test_object_particle_follows_the_final_consonant(self):
        from app.api.routes.reports import _object_particle

        # 받침 있음 → 을 / 없음 → 를. "채용를" 같은 비문을 막는다.
        self.assertEqual(_object_particle("채용"), "을")
        self.assertEqual(_object_particle("검색량"), "을")
        self.assertEqual(_object_particle("공시"), "를")
        self.assertEqual(_object_particle("주가"), "를")
        self.assertEqual(_object_particle("특허"), "를")
        self.assertEqual(_object_particle("증권사 리포트"), "를")

    def test_exclusion_note_uses_the_right_particle(self):
        text = _humanize_summary("hiring", {"summary": "x", "data_status": "missing"})
        self.assertIn("채용을 빼고", text)
        self.assertNotIn("채용를", text)

        text = _humanize_summary("dart", {"summary": "x", "data_status": "missing"})
        self.assertIn("공시를 빼고", text)

    def test_failed_source_says_it_could_not_be_analyzed(self):
        text = _humanize_summary("patent", {"summary": "x", "data_status": "failed"})

        self.assertIn("분석하지 못해", text)

    def test_scoring_source_keeps_its_own_summary(self):
        narrative = "특허 공개가 늘었습니다."
        text = _humanize_summary("patent", {"summary": narrative, "data_status": "ok"})

        self.assertEqual(text, narrative)

    def test_llm_narrative_is_left_alone(self):
        narrative = "삼성전자는 최근 반도체 수요 회복에 힘입어 검색 관심이 늘었습니다."
        for source in ("datalab", "patent", "hiring"):
            with self.subTest(source=source):
                self.assertEqual(
                    _humanize_summary(source, {"summary": narrative, "direction": "positive"}),
                    narrative,
                )


if __name__ == "__main__":
    unittest.main()
