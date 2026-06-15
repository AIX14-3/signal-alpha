"""Unit tests for the DataLab polarity refresh tool's pure logic.

DB and Gemini calls are out of scope (I/O); these cover prompt construction,
draft validation, and the dedup-against-existing guard.
"""

import unittest

from datalab_polarity_refresh import build_prompt, drop_existing, validate_draft


def _stock():
    return {"ticker": "005930", "name": "삼성전자", "sector": "반도체"}


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


class BuildPromptTests(unittest.TestCase):
    def test_prompt_includes_events_and_existing(self):
        disclosures = [
            {"report_name": "소송등의제기", "disclosure_type": "공정공시", "priority_reason": "분쟁"},
            {"report_name": "분기보고서", "disclosure_type": None, "priority_reason": None},
        ]
        existing = [{"keyword": "삼성전자 리콜", "polarity": "risk"}]
        prompt = build_prompt(_stock(), disclosures, existing)
        self.assertIn("소송등의제기", prompt)
        self.assertIn("사유: 분쟁", prompt)
        self.assertIn("삼성전자 리콜(risk)", prompt)
        self.assertIn("005930_RISK", prompt)

    def test_prompt_marks_no_existing(self):
        prompt = build_prompt(_stock(), [{"report_name": "유상증자결정"}], [])
        self.assertIn("(없음)", prompt)


class ValidateDraftTests(unittest.TestCase):
    def test_demand_only_is_allowed(self):
        # Refresh, unlike bootstrap, must NOT require a risk keyword.
        draft = validate_draft(_draft({"text": "삼성전자 신제품", "polarity": "demand"}), "005930")
        self.assertEqual(draft["categories"][0]["keywords"][0]["polarity"], "demand")

    def test_empty_categories_allowed(self):
        draft = validate_draft({"ticker": "005930", "categories": []}, "005930")
        self.assertEqual(draft["categories"], [])

    def test_invalid_polarity_rejected(self):
        with self.assertRaises(ValueError):
            validate_draft(_draft({"text": "x", "polarity": "bullish"}), "005930")

    def test_invalid_category_name_rejected(self):
        draft = _draft({"text": "x", "polarity": "risk"})
        draft["categories"][0]["category_name"] = "bad name!"
        with self.assertRaises(ValueError):
            validate_draft(draft, "005930")


class DropExistingTests(unittest.TestCase):
    def test_drops_mapped_keyword_and_empty_category(self):
        draft = _draft(
            {"text": "삼성전자 리콜", "polarity": "risk"},
            {"text": "삼성전자 불매", "polarity": "risk"},
        )
        out = drop_existing(draft, {"삼성전자 리콜"})
        self.assertEqual(len(out["categories"]), 1)
        texts = [k["text"] for k in out["categories"][0]["keywords"]]
        self.assertEqual(texts, ["삼성전자 불매"])

    def test_category_fully_existing_is_removed(self):
        draft = _draft({"text": "삼성전자 리콜", "polarity": "risk"})
        out = drop_existing(draft, {"삼성전자 리콜"})
        self.assertEqual(out["categories"], [])


if __name__ == "__main__":
    unittest.main()
