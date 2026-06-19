"""Tests for build_source_signal — the per-source replacement for the retired
cross-source AlternativeAggregator. Each SourceResult maps to its own
single-source AlternativeSignal (no weighting, no cross-source agreement)."""

import unittest

from app.aggregator.per_source import build_source_signal
from app.analyzers.config import AggregatorConfig
from app.schemas.source_result import EvidenceItem, SourceResult

CONFIG = AggregatorConfig(
    weights={"HIRING": 0.34, "PATENT": 0.33, "DATALAB": 0.33},
    confidence_base=0.15,
    confidence_per_source=0.20,
    partial_penalty=0.8,
    stale_penalty=0.85,
    sparse_penalty=0.8,
)


def _result(**kw):
    base = dict(
        source="PATENT",
        stock_code="005930",
        direction="positive",
        score=0.6,
        summary="patent up",
    )
    base.update(kw)
    return SourceResult(**base)


class BuildSourceSignalTest(unittest.TestCase):
    def test_positive_source_maps_to_single_source_signal(self):
        sig = build_source_signal(_result(), CONFIG)
        self.assertEqual(sig.direction, "positive")
        self.assertEqual(sig.score, 0.6)
        self.assertEqual(sig.available_sources, ["PATENT"])
        self.assertEqual(sig.missing_sources, [])
        self.assertEqual(sig.score_breakdown, {"PATENT": 0.6})
        self.assertEqual(sig.source_agreement, "MEDIUM")  # single source convention

    def test_confidence_is_single_source_formula(self):
        # base + per_source*1, no penalties, no agreement bonus.
        sig = build_source_signal(_result(), CONFIG)
        self.assertAlmostEqual(sig.confidence, 0.35, places=3)
        self.assertAlmostEqual(sig.consensus_score, 35.0, places=2)

    def test_partial_data_penalises_confidence(self):
        sig = build_source_signal(_result(data_status="partial"), CONFIG)
        self.assertAlmostEqual(sig.confidence, 0.35 * 0.8, places=3)

    def test_positive_evidence_from_evidence_items(self):
        sig = build_source_signal(
            _result(evidence_items=[EvidenceItem(title="신규 카테고리", summary="...")]),
            CONFIG,
        )
        self.assertTrue(any("신규 카테고리" in e for e in sig.positive_evidence))
        self.assertEqual(sig.caution_evidence, [])

    def test_negative_source_goes_to_caution(self):
        sig = build_source_signal(
            _result(direction="negative", score=-0.5, evidence_items=[
                EvidenceItem(title="검색 급락", summary="...")
            ]),
            CONFIG,
        )
        self.assertEqual(sig.direction, "negative")
        self.assertEqual(sig.positive_evidence, [])
        self.assertTrue(any("검색 급락" in e for e in sig.caution_evidence))

    def test_failed_source_is_unknown_and_flagged(self):
        sig = build_source_signal(
            _result(data_status="failed", direction="unknown", score=0.0,
                    risk_flags=["analyzer_error"]),
            CONFIG,
        )
        self.assertEqual(sig.direction, "unknown")
        self.assertEqual(sig.score, 0.0)
        self.assertEqual(sig.confidence, 0.0)
        self.assertEqual(sig.available_sources, [])
        self.assertEqual(sig.missing_sources, ["PATENT"])
        self.assertEqual(sig.score_breakdown, {})
        self.assertTrue(sig.caution_evidence)

    def test_out_of_range_score_demoted_to_unknown(self):
        # A producer leaking a 0-100 scale (e.g. 55) violates the [-1, +1]
        # contract → published as unknown with a scale-violation flag.
        sig = build_source_signal(_result(score=55.0), CONFIG)
        self.assertEqual(sig.direction, "unknown")
        self.assertIn("score_out_of_range", sig.risk_flags)
        self.assertEqual(sig.available_sources, [])


if __name__ == "__main__":
    unittest.main()
