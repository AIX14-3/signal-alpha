"""Unit tests for the DataLab polarity *bootstrap* tool's pure logic (Stage 1).

DB and Gemini calls are I/O and out of scope; these cover the Scope-A additions:
confidence parsing/clamping, rationale capture, and the prohibited-advice guard.
"""

import unittest

from datalab_polarity_keywords import _DEFAULT_CONFIDENCE, validate_draft


def _draft(*keywords):
    return {
        "ticker": "005930",
        "categories": [
            {
                "category_name": "005930_RISK",
                "keyword_group": "005930_RISK",
                "keywords": list(keywords),
                "linked_stocks": [{"ticker": "005930", "weight": 1.0}],
            }
        ],
    }


class PolarityProvenanceTests(unittest.TestCase):
    def test_confidence_and_rationale_captured(self):
        draft = validate_draft(
            _draft({"text": "삼성전자 리콜", "polarity": "risk", "confidence": 0.9, "rationale": "악재 검색 의도"}),
            "005930",
        )
        kw = draft["categories"][0]["keywords"][0]
        self.assertEqual(kw["confidence"], 0.9)
        self.assertEqual(kw["rationale"], "악재 검색 의도")

    def test_missing_confidence_defaults(self):
        draft = validate_draft(_draft({"text": "삼성전자 리콜", "polarity": "risk"}), "005930")
        self.assertEqual(draft["categories"][0]["keywords"][0]["confidence"], _DEFAULT_CONFIDENCE)

    def test_confidence_clamped_to_unit_range(self):
        draft = validate_draft(
            _draft(
                {"text": "삼성전자 리콜", "polarity": "risk", "confidence": 5},
                {"text": "삼성전자 신제품", "polarity": "demand", "confidence": -1},
            ),
            "005930",
        )
        kws = draft["categories"][0]["keywords"]
        self.assertEqual(kws[0]["confidence"], 1.0)
        self.assertEqual(kws[1]["confidence"], 0.0)

    def test_non_numeric_confidence_defaults(self):
        draft = validate_draft(
            _draft({"text": "삼성전자 리콜", "polarity": "risk", "confidence": "high"}), "005930"
        )
        self.assertEqual(draft["categories"][0]["keywords"][0]["confidence"], _DEFAULT_CONFIDENCE)

    def test_investment_advice_rationale_rejected(self):
        for bad in ("지금 매수 추천", "target price 90000", "강력 매도 신호"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_draft(
                        _draft({"text": "삼성전자 리콜", "polarity": "risk", "rationale": bad}),
                        "005930",
                    )


if __name__ == "__main__":
    unittest.main()
