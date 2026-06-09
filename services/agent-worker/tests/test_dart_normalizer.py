import unittest

from app.orchestrator.dart_normalizer import classify_dart_report, make_dart_event_hash


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
