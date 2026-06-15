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


if __name__ == "__main__":
    unittest.main()
