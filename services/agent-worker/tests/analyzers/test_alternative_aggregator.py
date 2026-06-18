import unittest

from app.aggregator import AlternativeAggregator
from app.analyzers.config import AggregatorConfig
from app.schemas.source_result import SourceResult

CONFIG = AggregatorConfig(
    weights={"HIRING": 0.34, "PATENT": 0.33, "DATALAB": 0.33},
    positive_threshold=0.2,
    negative_threshold=-0.2,
    confidence_base=0.3,
    confidence_per_source=0.35,
)


def _sr(source, direction, score, status="ok"):
    return SourceResult(
        source=source,
        stock_code="005930",
        direction=direction,
        score=score,
        summary="",
        data_status=status,
    )


class AlternativeAggregatorTest(unittest.TestCase):
    def setUp(self):
        self.aggregator = AlternativeAggregator(CONFIG)

    def test_agreeing_sources_blend_high(self):
        signal = self.aggregator.merge(
            [_sr("PATENT", "positive", 0.6), _sr("DATALAB", "positive", 0.8)]
        )
        self.assertEqual(signal.direction, "positive")
        self.assertEqual(signal.source_agreement, "HIGH")
        self.assertAlmostEqual(signal.score, 0.7, places=3)
        self.assertEqual(set(signal.score_breakdown), {"PATENT", "DATALAB"})

    def test_conflicting_sources_are_mixed_low(self):
        signal = self.aggregator.merge(
            [_sr("PATENT", "positive", 0.6), _sr("DATALAB", "negative", -0.6)]
        )
        self.assertEqual(signal.direction, "mixed")
        self.assertEqual(signal.source_agreement, "LOW")

    def test_failed_source_is_missing_and_excluded(self):
        signal = self.aggregator.merge(
            [_sr("PATENT", "positive", 0.6), _sr("DATALAB", "unknown", 0.0, "failed")]
        )
        self.assertEqual(signal.direction, "positive")
        self.assertEqual(signal.available_sources, ["PATENT"])
        self.assertEqual(signal.missing_sources, ["DATALAB"])
        self.assertEqual(signal.source_agreement, "MEDIUM")
        self.assertIn("missing_datalab", signal.risk_flags)
        self.assertAlmostEqual(signal.score, 0.6, places=3)
        # failed source must NOT appear as a (misleading) score in the breakdown
        self.assertEqual(set(signal.score_breakdown), {"PATENT"})

    def test_all_failed_is_unknown_low(self):
        signal = self.aggregator.merge(
            [
                _sr("PATENT", "unknown", 0.0, "failed"),
                _sr("DATALAB", "unknown", 0.0, "failed"),
            ]
        )
        self.assertEqual(signal.direction, "unknown")
        self.assertEqual(signal.source_agreement, "LOW")
        self.assertEqual(signal.score, 0.0)
        self.assertEqual(signal.available_sources, [])

    def test_partial_data_lowers_confidence(self):
        full = self.aggregator.merge(
            [_sr("PATENT", "positive", 0.6), _sr("DATALAB", "positive", 0.6)]
        )
        partial = self.aggregator.merge(
            [
                _sr("PATENT", "positive", 0.6),
                _sr("DATALAB", "positive", 0.6, "partial"),
            ]
        )
        self.assertLess(partial.confidence, full.confidence)

    def test_consensus_and_evidence_separation(self):
        # one positive source + one missing → positive_evidence has the bull fact,
        # caution_evidence notes the missing source; consensus_score is 0-100.
        pos = SourceResult(
            source="PATENT", stock_code="005930", direction="positive", score=0.6,
            summary="특허 출원 증가",
        )
        signal = self.aggregator.merge(
            [pos, _sr("DATALAB", "unknown", 0.0, "failed")]
        )
        self.assertGreater(signal.consensus_score, 0.0)
        self.assertLessEqual(signal.consensus_score, 100.0)
        self.assertEqual(signal.alignment_rate, signal.source_agreement)
        self.assertTrue(any("PATENT" in e for e in signal.positive_evidence))
        self.assertTrue(any("DATALAB" in e and "누락" in e for e in signal.caution_evidence))

    def test_negative_source_is_caution_evidence(self):
        neg = SourceResult(
            source="DATALAB", stock_code="005930", direction="negative", score=-0.6,
            summary="검색 관심 급감",
        )
        signal = self.aggregator.merge([_sr("PATENT", "positive", 0.6), neg])
        self.assertTrue(any("DATALAB" in e for e in signal.caution_evidence))
        self.assertTrue(any("소스 간 방향" in e for e in signal.caution_evidence))

    def test_single_source_mixed_stays_mixed(self):
        """단일 소스가 'mixed'를 반환하면 통합 방향도 'mixed'여야 한다.

        이전: `and len(available) > 1` 가드로 인해 단일 소스 mixed가 score
        임계값 분기로 떨어져 'neutral'로 둔갑하는 버그가 있었음.
        """
        signal = self.aggregator.merge([_sr("PATENT", "mixed", 0.05)])
        self.assertEqual(signal.direction, "mixed")

    def test_risk_search_flag_surfaces_as_caution(self):
        # risk_search 는 available(ok) DATALAB 음성 소스에 붙는다 → caution 으로 표면화.
        datalab = SourceResult(
            source="DATALAB", stock_code="005930", direction="negative", score=-0.5,
            summary="위험 키워드 급등", risk_flags=["risk_search"],
        )
        signal = self.aggregator.merge([_sr("PATENT", "positive", 0.6), datalab])
        self.assertTrue(
            any("위험 키워드 검색 급등(악재 신호)" in e for e in signal.caution_evidence)
        )

    def test_zero_weights_warn_but_still_equal_blend(self):
        # L3 guard: when no source has a configured weight, the aggregator must
        # still return the equal-blend score (unchanged behavior) but emit a
        # WARNING so the misconfiguration is visible rather than silently masked.
        unconfigured = AggregatorConfig(
            weights={},  # nothing configured → every weight resolves to 0.0
            positive_threshold=0.2,
            negative_threshold=-0.2,
            confidence_base=0.3,
            confidence_per_source=0.35,
        )
        aggregator = AlternativeAggregator(unconfigured)
        results = [_sr("PATENT", "positive", 0.6), _sr("DATALAB", "positive", 0.8)]

        with self.assertLogs(
            "app.aggregator.alternative", level="WARNING"
        ) as captured:
            signal = aggregator.merge(results)

        # Warning fired about the equal-blend fallback.
        self.assertTrue(any("equal blend" in m for m in captured.output))
        # Equal-blend score is unchanged: mean of 0.6 and 0.8.
        self.assertAlmostEqual(signal.score, 0.7, places=3)

    def test_analyzer_error_flag_surfaces_for_missing_source(self):
        # analyzer_error 는 실패=missing 소스에 붙는다 → 누락 caution 의 사유로 표면화.
        failed = SourceResult(
            source="PATENT", stock_code="005930", direction="unknown", score=0.0,
            summary="분석 실패", data_status="failed", risk_flags=["analyzer_error"],
        )
        signal = self.aggregator.merge([_sr("DATALAB", "positive", 0.6), failed])
        self.assertTrue(
            any("PATENT" in e and "분석기 오류로 제외됨" in e for e in signal.caution_evidence)
        )


    def test_out_of_range_score_is_excluded_and_flagged(self):
        # A source leaking a 0-100 scale (e.g. DART's native 50 = neutral) must
        # NOT be blended into the [-1, +1] weighted score. It is demoted to a
        # failed source: excluded, surfaced as missing + caution, and logged.
        with self.assertLogs("app.aggregator.alternative", level="ERROR") as captured:
            signal = self.aggregator.merge(
                [_sr("PATENT", "positive", 0.6), _sr("DATALAB", "positive", 50.0)]
            )
        self.assertTrue(any("outside [-1, +1]" in m for m in captured.output))
        # Only the valid source contributes — 50.0 never reaches _weighted_score.
        self.assertEqual(signal.available_sources, ["PATENT"])
        self.assertEqual(signal.missing_sources, ["DATALAB"])
        self.assertAlmostEqual(signal.score, 0.6, places=3)
        self.assertEqual(set(signal.score_breakdown), {"PATENT"})
        self.assertIn("score_out_of_range", signal.risk_flags)
        self.assertTrue(
            any("DATALAB" in e and "규약 위반" in e for e in signal.caution_evidence)
        )

    def test_default_confidence_is_earned_not_saturated(self):
        # With shipped defaults, confidence must be EARNED (rises with each
        # agreeing source) and never reach 100% even on a perfect 3-source
        # agreement — §10 avoids overclaiming certainty on an informational signal.
        cfg = AggregatorConfig(
            weights={"HIRING": 0.34, "PATENT": 0.33, "DATALAB": 0.33}
        )
        agg = AlternativeAggregator(cfg)
        one = agg.merge([_sr("HIRING", "positive", 0.6)])
        two = agg.merge(
            [_sr("HIRING", "positive", 0.6), _sr("PATENT", "positive", 0.6)]
        )
        three = agg.merge(
            [
                _sr("HIRING", "positive", 0.6),
                _sr("PATENT", "positive", 0.6),
                _sr("DATALAB", "positive", 0.6),
            ]
        )
        self.assertLess(three.confidence, 1.0)  # never saturates at 100%
        self.assertLess(one.confidence, two.confidence)  # earned, monotonic
        self.assertLess(two.confidence, three.confidence)

    def test_boundary_scores_pass_just_outside_rejected(self):
        # ±1.0 are valid (tolerance absorbs float rounding); just past the bound
        # is a violation and gets excluded.
        ok = self.aggregator.merge(
            [_sr("PATENT", "positive", 1.0), _sr("DATALAB", "negative", -1.0)]
        )
        self.assertEqual(set(ok.available_sources), {"PATENT", "DATALAB"})
        self.assertNotIn("score_out_of_range", ok.risk_flags)

        rejected = self.aggregator.merge(
            [_sr("PATENT", "positive", 0.6), _sr("DATALAB", "positive", 1.0001)]
        )
        self.assertEqual(rejected.available_sources, ["PATENT"])
        self.assertIn("score_out_of_range", rejected.risk_flags)


if __name__ == "__main__":
    unittest.main()
