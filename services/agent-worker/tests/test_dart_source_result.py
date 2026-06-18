import unittest
from datetime import date

from app.analyzers.dart.source_result import build_dart_analysis_result


class DartSourceResultTest(unittest.TestCase):
    def test_builds_neutral_ok_result_from_periodic_report(self):
        result = build_dart_analysis_result(
            [
                {
                    "id": 10,
                    "event_type": "periodic_report",
                    "event_date": date(2026, 6, 8),
                    "signal_direction": "neutral",
                    "impact_level": "medium",
                    "title": "Quarterly report",
                    "summary": "DART disclosure: Quarterly report",
                    "evidence_url": "https://dart.example/10",
                    "needs_review": False,
                }
            ]
        )

        self.assertEqual(result.direction, "neutral")
        self.assertEqual(result.score, 0.0)
        self.assertFalse(result.needs_review)
        self.assertEqual(result.method_detail["data_status"], "ok")
        self.assertEqual(result.method_detail["event_count"], 1)

    def test_marks_correction_as_partial_review_result(self):
        result = build_dart_analysis_result(
            [
                {
                    "id": 11,
                    "event_type": "correction",
                    "event_date": date(2026, 6, 9),
                    "signal_direction": "neutral",
                    "impact_level": "low",
                    "title": "Correction report",
                    "summary": "DART disclosure: Correction report",
                    "evidence_url": "https://dart.example/11",
                    "needs_review": True,
                }
            ]
        )

        self.assertEqual(result.direction, "neutral")
        self.assertTrue(result.needs_review)
        self.assertEqual(result.method_detail["data_status"], "partial")
        self.assertIn("correction_disclosure", result.risk_flags)

    def test_positive_and_negative_events_resolve_to_mixed(self):
        result = build_dart_analysis_result(
            [
                {
                    "id": 12,
                    "event_type": "supply_contract",
                    "event_date": date(2026, 6, 9),
                    "signal_direction": "positive",
                    "impact_level": "high",
                    "title": "Supply contract",
                    "needs_review": False,
                },
                {
                    "id": 13,
                    "event_type": "financing",
                    "event_date": date(2026, 6, 9),
                    "signal_direction": "negative",
                    "impact_level": "high",
                    "title": "Financing disclosure",
                    "needs_review": False,
                },
            ]
        )

        self.assertEqual(result.direction, "mixed")
        self.assertTrue(result.needs_review)
        self.assertEqual(result.score, 0.0)

    def test_positive_high_impact_event_scores_on_signed_unit_range(self):
        result = build_dart_analysis_result(
            [
                {
                    "id": 14,
                    "event_type": "supply_contract",
                    "event_date": date(2026, 6, 9),
                    "signal_direction": "positive",
                    "impact_level": "high",
                    "title": "Supply contract",
                    "needs_review": False,
                }
            ]
        )

        self.assertEqual(result.direction, "positive")
        self.assertAlmostEqual(result.score, 0.3)
