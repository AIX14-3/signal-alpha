import unittest
from pathlib import Path

from app.analyzers.dart.rules import classify_dart_report, make_dart_event_hash


AGENT_WORKER_ROOT = Path(__file__).resolve().parents[1]


class DartNormalizerTest(unittest.TestCase):
    def test_classifies_periodic_report_as_neutral_medium(self):
        classification = classify_dart_report("분기보고서")

        self.assertEqual(classification.event_type, "periodic_report")
        self.assertEqual(classification.signal_direction, "neutral")
        self.assertEqual(classification.impact_level, "medium")

    def test_classifies_major_event_as_mixed_high(self):
        classification = classify_dart_report("주요사항보고서")

        self.assertEqual(classification.event_type, "material_event")
        self.assertEqual(classification.signal_direction, "mixed")
        self.assertEqual(classification.impact_level, "high")

    # ------- B-lite: 행위형 공시 극성 (제목=사실) -------

    def test_treasury_buyback_is_positive(self):
        c = classify_dart_report("주요사항보고서(자기주식취득결정)")
        self.assertEqual(c.event_type, "treasury_buyback")
        self.assertEqual(c.signal_direction, "positive")

    def test_capital_increase_is_negative(self):
        c = classify_dart_report("유상증자결정")
        self.assertEqual(c.event_type, "capital_increase")
        self.assertEqual(c.signal_direction, "negative")

    def test_dividend_is_positive(self):
        c = classify_dart_report("현금ㆍ현물배당결정")
        self.assertEqual(c.signal_direction, "positive")

    def test_action_polarity_beats_material_event_wrapper(self):
        # 주요사항보고서로 감싸여도 행위(자사주매입)가 먼저 매칭돼 mixed 가 아니라 positive.
        c = classify_dart_report("주요사항보고서(자기주식취득결정)")
        self.assertNotEqual(c.event_type, "material_event")
        self.assertEqual(c.signal_direction, "positive")

    def test_treasury_disposal_provisional_negative(self):
        c = classify_dart_report("주요사항보고서(자기주식처분결정)")
        self.assertEqual(c.event_type, "treasury_disposal")
        self.assertEqual(c.signal_direction, "negative")

    def test_insider_ownership_title_stays_neutral(self):
        # 지분보고서는 제목만으론 매수/매도 불명 → 중립(방향은 dart_ownership_change 이벤트로 유입).
        c = classify_dart_report("임원ㆍ주요주주특정증권등소유상황보고서")
        self.assertEqual(c.event_type, "insider_ownership")
        self.assertEqual(c.signal_direction, "neutral")

    def test_classifies_correction_as_low_review_event(self):
        classification = classify_dart_report("[기재정정]분기보고서")

        self.assertEqual(classification.event_type, "correction")
        self.assertEqual(classification.impact_level, "low")
        self.assertTrue(classification.needs_review)

    def test_event_hash_is_stable(self):
        first = make_dart_event_hash("005930", "202606080001", "분기보고서")
        second = make_dart_event_hash("005930", "202606080001", "분기보고서")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_classifies_korean_correction_marker(self):
        classification = classify_dart_report("[정정]Quarterly report")

        self.assertEqual(classification.event_type, "correction")
        self.assertTrue(classification.needs_review)

    def test_classifies_correction_flag_as_correction_event(self):
        classification = classify_dart_report("Quarterly report", is_correction=True)

        self.assertEqual(classification.event_type, "correction")
        self.assertTrue(classification.needs_review)

    def test_normalizer_source_does_not_keep_mojibake_markers(self):
        source = (AGENT_WORKER_ROOT / "app" / "analyzers" / "dart" / "rules.py").read_text(
            encoding="utf-8"
        )

        mojibake_markers = (
            "\u4e8c\uc1e0\uc2b5?\uc774\ube46\u8e42\ub2ff\ud000",
            "?\uae3e\uc36d",
            "?\uc790\ubf7d\u8e42\ub2ff\ud000",
            "\u8adb\uc12c\uae30\u8e42\ub2ff\ud000",
            "\u907a\uae3e\uae30\u8e42\ub2ff\ud000",
            "\u6e72\uacd7\ubf7d\ufffd\u8adb\uace8\ubf0c\u8b70\uace8\ub0ab\u6028\uc878\uae4c",
            "\u6e72\uacd7\uc631?\ubea4\uc819",
            "?\ubea4\uc819",
        )
        for marker in mojibake_markers:
            self.assertNotIn(marker, source)
