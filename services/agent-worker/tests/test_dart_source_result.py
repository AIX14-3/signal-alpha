import unittest
from datetime import date

from app.analyzers.dart.source_result import build_dart_analysis_result


class DartSourceResultTest(unittest.TestCase):
    """Phase 0(#546): 피처 전용 — direction='unknown', score=0, data_status='no_signal'."""

    def test_empty_events_return_no_signal(self):
        result = build_dart_analysis_result([])
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.score, 0.0)
        self.assertFalse(result.needs_review)
        self.assertEqual(result.method_detail["data_status"], "no_signal")
        self.assertEqual(result.method_detail["event_count"], 0)

    def test_features_only_no_verdict(self):
        result = build_dart_analysis_result(
            [
                {
                    "id": 10,
                    "event_type": "periodic_report",
                    "event_date": date(2026, 6, 8),
                    "signal_direction": "neutral",
                    "impact_level": "medium",
                    "title": "Quarterly report",
                    "needs_review": False,
                }
            ]
        )

        # 판정 없음 — 메타러너 return 채널이 산출.
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.method_detail["data_status"], "no_signal")
        self.assertEqual(result.method_detail["event_count"], 1)
        # 서술 피처는 보존.
        self.assertEqual(result.method_detail["direction_counts"], {"neutral": 1})
        self.assertEqual(result.method_detail["event_type_counts"], {"periodic_report": 1})
        self.assertEqual(result.method_detail["impact_level_counts"], {"medium": 1})

    def test_correction_sets_quality_flag_and_review(self):
        result = build_dart_analysis_result(
            [
                {
                    "id": 11,
                    "event_type": "correction",
                    "event_date": date(2026, 6, 9),
                    "signal_direction": "neutral",
                    "impact_level": "low",
                    "title": "Correction report",
                    "needs_review": True,
                }
            ]
        )

        # 판정은 여전히 없음(unknown), 데이터품질 플래그/리뷰만 서술.
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.method_detail["data_status"], "no_signal")
        self.assertTrue(result.needs_review)
        self.assertIn("correction_disclosure", result.risk_flags)

    def test_mixed_direction_events_stay_unknown(self):
        result = build_dart_analysis_result(
            [
                {
                    "id": 12,
                    "event_type": "supply_contract",
                    "signal_direction": "positive",
                    "impact_level": "high",
                    "title": "Supply contract",
                    "needs_review": False,
                },
                {
                    "id": 13,
                    "event_type": "financing",
                    "signal_direction": "negative",
                    "impact_level": "high",
                    "title": "Financing disclosure",
                    "needs_review": False,
                },
            ]
        )

        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.method_detail["direction_counts"], {"positive": 1, "negative": 1})

    # ------- Wave 2: derived_features (additive) -------

    def test_derived_features_additive_values(self):
        result = build_dart_analysis_result(
            [
                {
                    "id": 20,
                    "event_type": "supply_contract",
                    "event_date": date(2026, 6, 8),
                    "signal_direction": "positive",
                    "impact_level": "high",
                    "is_official": True,
                    "needs_review": False,
                },
                {
                    "id": 21,
                    "event_type": "financing",
                    "event_date": date(2026, 6, 10),
                    "signal_direction": "negative",
                    "impact_level": "high",
                    "is_official": True,
                    "needs_review": False,
                },
                {
                    "id": 22,
                    "event_type": "correction",
                    "event_date": date(2026, 6, 5),
                    "signal_direction": "neutral",
                    "impact_level": "low",
                    "needs_review": True,
                },
            ]
        )

        features = result.method_detail["derived_features"]
        self.assertEqual(features["total_events"], 3)
        self.assertEqual(features["distinct_event_types"], 3)
        self.assertEqual(features["high_impact_count"], 2)  # medium/high 이상만
        self.assertEqual(features["impact_weighted_count"], 7.0)  # 3+3+1
        self.assertEqual(features["correction_count"], 1)
        self.assertEqual(features["needs_review_count"], 1)
        self.assertEqual(features["official_count"], 2)
        self.assertEqual(features["latest_event_date"], "2026-06-10")
        # 숫자 판정은 여전히 없음 — 피처만.
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.score, 0.0)

    def test_empty_events_derived_features_zeroed(self):
        features = build_dart_analysis_result([]).method_detail["derived_features"]
        self.assertEqual(features["total_events"], 0)
        self.assertEqual(features["impact_weighted_count"], 0.0)
        self.assertIsNone(features["latest_event_date"])

    def test_existing_method_detail_keys_unchanged(self):
        # Wave 3 융합이 그대로 읽는 기존 키/형태는 불변 — derived_features 만 additive.
        populated = build_dart_analysis_result(
            [
                {
                    "id": 30,
                    "event_type": "periodic_report",
                    "event_date": date(2026, 6, 8),
                    "signal_direction": "neutral",
                    "impact_level": "medium",
                    "needs_review": False,
                }
            ]
        ).method_detail
        self.assertEqual(
            set(populated),
            {
                "source",
                "data_status",
                "event_count",
                "direction_counts",
                "event_type_counts",
                "impact_level_counts",
                "events",
                "derived_features",
            },
        )
        empty = build_dart_analysis_result([]).method_detail
        self.assertEqual(
            set(empty),
            {"source", "data_status", "event_count", "events", "derived_features"},
        )
