"""Unit tests for the report route's source-block mapping.

Locks the C안 Phase 2 contract: the worker's ``score_breakdown`` exposes the
alternative sources as flat top-level keys (HIRING/DATALAB), not nested under a
single ALTERNATIVE umbrella. The report page (the live frontend consumer) reads
them through ``_source_block`` keyed by ``_SOURCE_TO_BREAKDOWN``.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.api.routes.reports import (
    _derive_dart_points,
    _derive_report_points,
    _humanize_dart_summary,
    _humanize_report_summary,
    _PREDICTION_RATE_SOURCES,
    _prediction_rate_block,
    _SOURCE_TO_BREAKDOWN,
    _source_block,
)


_BREAKDOWN = {
    "DART": {"direction": "neutral", "score_100": 50.0, "data_status": "ok", "summary": "공시 중립"},
    "PRICE": {"direction": "positive", "score_100": 72.0, "data_status": "ok"},
    "HIRING": {"direction": "positive", "score_100": 64.0, "data_status": "ok", "summary": "채용 증가"},
    "DATALAB": {"direction": "negative", "score_100": 30.0, "data_status": "ok"},
}


class SourceBlockMappingTest(unittest.TestCase):
    def test_alt_sources_map_to_their_own_flat_keys(self):
        # 과거엔 둘 다 ALTERNATIVE 로 묶였다 → 이제 각자 자기 키.
        self.assertEqual(_SOURCE_TO_BREAKDOWN["hiring"], "HIRING")
        self.assertEqual(_SOURCE_TO_BREAKDOWN["datalab"], "DATALAB")

    def test_reads_flat_hiring_and_datalab_blocks(self):
        hiring = _source_block("hiring", _BREAKDOWN)
        self.assertEqual(hiring["direction"], "positive")
        self.assertEqual(hiring["score"], 64)
        self.assertEqual(hiring["data_status"], "ok")
        self.assertEqual(hiring["summary"], "채용 증가")

        datalab = _source_block("datalab", _BREAKDOWN)
        self.assertEqual(datalab["direction"], "negative")
        self.assertEqual(datalab["score"], 30)

    def test_price_and_dart_unchanged(self):
        self.assertEqual(_source_block("price", _BREAKDOWN)["score"], 72)
        self.assertEqual(_source_block("dart", _BREAKDOWN)["score"], 50)

    def test_absent_source_is_missing(self):
        block = _source_block("hiring", {"DART": _BREAKDOWN["DART"]})
        self.assertEqual(block["direction"], "unknown")
        self.assertEqual(block["data_status"], "missing")
        self.assertIsNone(block["score"])


_SOURCE_PREDICTIONS = {
    "SRC": {"final_score": 0.04, "score_100": 70.0, "direction": "positive"},
    "SRC_PRICE": {"final_score": 0.02, "score_100": 60.0, "direction": "positive"},
    "SRC_DART": {"final_score": 0.03, "score_100": 64.0, "direction": "positive"},
    "SRC_DATALAB": {"final_score": -0.02, "score_100": 41.0, "direction": "negative"},
    "SRC_HIRING": {"final_score": 0.01, "score_100": 55.0, "direction": "positive"},
    "SRC_PATENT": {"final_score": 0.0, "score_100": 50.0, "direction": "neutral"},
    "SRC_REPORT": {"final_score": 0.05, "score_100": 73.0, "direction": "positive"},
}


class PredictionRateBlockTest(unittest.TestCase):
    def test_exposes_price_and_five_public_data_rates(self):
        # 주가 1 + 공공데이터 5(dart/datalab/hiring/patent/report) = per-source 6개.
        self.assertEqual(
            _PREDICTION_RATE_SOURCES, ("price", "dart", "datalab", "hiring", "patent", "report")
        )

    def test_reads_score_100_and_direction(self):
        block = _prediction_rate_block("dart", _SOURCE_PREDICTIONS)
        self.assertEqual(block["score"], 64)
        self.assertEqual(block["direction"], "positive")
        self.assertEqual(block["data_status"], "ok")
        report = _prediction_rate_block("report", _SOURCE_PREDICTIONS)
        self.assertEqual(report["score"], 73)

    def test_absent_prediction_is_missing(self):
        block = _prediction_rate_block("patent", {"SRC_DART": _SOURCE_PREDICTIONS["SRC_DART"]})
        self.assertEqual(block["data_status"], "missing")
        self.assertIsNone(block["score"])


_DART_TERSE = (
    "DART 공시 21건 피처 산출(유형 {'annual_report': 1}, 방향 {'unknown': 21}). "
    "판정은 학습형 메타러너가 수행."
)


class HumanizeDartSummaryTest(unittest.TestCase):
    def test_rewrites_machine_summary_without_asserting_direction(self):
        out = _humanize_dart_summary({"summary": _DART_TERSE, "direction": "unknown"})
        self.assertIn("최근 수집된 공시", out)
        self.assertIn("학습형 모델", out)
        self.assertIn("근거 자료", out)
        # Phase 0 features-only — 방향을 단정하지 않는다.
        self.assertNotIn("피처 산출", out)
        self.assertNotIn("메타러너", out)
        # 발행 score_breakdown 의 건수는 signal_events 와 어긋날 수 있어 헤드라인엔 넣지 않는다.
        self.assertNotIn("21건", out)

    def test_passes_through_natural_language(self):
        natural = "삼성전자는 1분기 호실적 공시로 긍정적 신호를 보였습니다."
        self.assertEqual(_humanize_dart_summary({"summary": natural}), natural)

    def test_appends_review_note_on_correction_flag(self):
        out = _humanize_dart_summary(
            {"summary": _DART_TERSE, "risk_flags": ["correction_disclosure"]}
        )
        self.assertIn("정정·검토", out)

    def test_none_summary_returns_none(self):
        self.assertIsNone(_humanize_dart_summary({}))


class DeriveDartPointsTest(unittest.TestCase):
    def _items(self):
        return [
            {"title": "사업보고서 제출", "event_date": "2026-04-15", "direction": "positive", "impact_level": "high"},
            {"title": "단일판매·공급계약", "event_date": "2026-04-10", "direction": "positive", "impact_level": "medium"},
            {"title": "최대주주 변경", "event_date": "2026-04-01", "direction": "negative", "impact_level": "low"},
            {"title": "기타 안내", "event_date": "2026-03-20", "direction": "unknown", "impact_level": "low"},
        ]

    def test_summarizes_count_and_direction_distribution(self):
        points = _derive_dart_points(self._items(), {})
        self.assertIn("최근 공시 4건을 분석했습니다.", points)
        dist = next(p for p in points if "분류됐습니다" in p)
        self.assertIn("긍정 2건", dist)
        self.assertIn("부정 1건", dist)
        self.assertIn("분류 보류 1건", dist)

    def test_flags_high_impact_and_quotes_notable(self):
        points = _derive_dart_points(self._items(), {})
        self.assertTrue(any("중요도 높은 공시 1건" in p for p in points))
        # 고임팩트 공시를 제목·날짜와 함께 인용.
        self.assertTrue(any("[2026-04-15] 사업보고서 제출" in p for p in points))

    def test_appends_review_note(self):
        points = _derive_dart_points(self._items(), {"risk_flags": ["review_required:correction"]})
        self.assertTrue(any("정정·검토" in p for p in points))

    def test_empty_items_returns_empty(self):
        self.assertEqual(_derive_dart_points([], {}), [])

    def test_dedupes_identical_notable_disclosures(self):
        # 같은 날 동일 제목(예: 임원 소유상황보고서 다건)은 한 번만 인용한다.
        dup = [
            {"title": "임원ㆍ주요주주특정증권등소유상황보고서", "event_date": "2026-06-25", "direction": "neutral", "impact_level": "low"},
            {"title": "임원ㆍ주요주주특정증권등소유상황보고서", "event_date": "2026-06-25", "direction": "neutral", "impact_level": "low"},
        ]
        points = _derive_dart_points(dup, {})
        quoted = [p for p in points if p.startswith("[2026-06-25]")]
        self.assertEqual(len(quoted), 1)


_REPORT_TERSE = (
    "미래에셋, 삼성증권 자료의 밸류에이션 fact 기준 데이터 방향성은 긍정 방향입니다. "
    "소스 간 일치도와 원문 근거 확인이 필요합니다."
)


class HumanizeReportSummaryTest(unittest.TestCase):
    def test_rewrites_machine_summary(self):
        out = _humanize_report_summary({"summary": _REPORT_TERSE})
        self.assertIn("목표주가·밸류에이션", out)
        self.assertIn("학습형 모델", out)
        self.assertIn("밸류에이션", out)
        self.assertNotIn("밸류에이션 fact", out)
        self.assertNotIn("소스 간 일치도", out)

    def test_passes_through_natural_language(self):
        natural = "목표주가 상향이 잇따르며 투자의견이 개선되고 있습니다."
        self.assertEqual(_humanize_report_summary({"summary": natural}), natural)

    def test_none_summary_returns_none(self):
        self.assertIsNone(_humanize_report_summary({}))


class DeriveReportPointsTest(unittest.TestCase):
    def _valuation(self):
        return {
            "event_count": 3,
            "target_price": 95000.0,
            "methodology": "PER",
            "implied_multiple_avg": 12.5,
            "peer_group": ["SK하이닉스", "마이크론", "TSMC"],
        }

    def test_summarizes_valuation_facts(self):
        points = _derive_report_points([], {"direction": "positive"}, self._valuation())
        self.assertIn("증권사 리포트 3건을 집계했습니다.", points)
        self.assertTrue(any("긍정적" in p for p in points))
        self.assertTrue(any("95,000원" in p for p in points))
        self.assertTrue(any("PER" in p for p in points))
        self.assertTrue(any("12.5배" in p for p in points))
        self.assertTrue(any("비교 기업: SK하이닉스, 마이크론, TSMC." == p for p in points))

    def test_counts_from_items_when_event_count_absent(self):
        items = [{"title": "a"}, {"title": "b"}]
        points = _derive_report_points(items, {}, {"target_price": 1000})
        self.assertIn("증권사 리포트 2건을 집계했습니다.", points)

    def test_applied_multiple_fallback_and_review_flag(self):
        points = _derive_report_points(
            [],
            {"direction": "neutral", "risk_flags": ["valuation_review_required"]},
            {"applied_multiple": 9},
        )
        self.assertTrue(any("9배" in p for p in points))
        self.assertTrue(any("밸류에이션 검토" in p for p in points))

    def test_skips_unknown_methodology_and_trims_multiple_decimals(self):
        points = _derive_report_points(
            [], {"direction": "positive"}, {"methodology": "unknown", "implied_multiple_avg": 86.8687}
        )
        self.assertFalse(any("방법론" in p for p in points))
        self.assertTrue(any("86.9배" in p for p in points))

    def test_empty_inputs_returns_empty(self):
        self.assertEqual(_derive_report_points([], {}, None), [])


if __name__ == "__main__":
    unittest.main()
