"""위험 플래그가 화면에 영문 식별자로 새어 나가지 않는지 검증.

사용자 리포트: 근거 카드 아래 경고줄에 `search_spike`, `hiring_decline`,
`review_required:correction` 같은 내부 식별자가 그대로 노출됐다. `_RISK_FLAG_KO` 에
키가 없으면 원문을 그대로 내보내던 폴백 때문이다.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.api.routes.reports import _evidence_list, _risk_flag_ko

# 분석기 6종이 실제로 내보내는 값 전부(analyzers/*/rules.py 의 risk_flags.append).
_ALL_FLAGS = [
    "no_data",
    "stale_data",
    "insufficient_history",
    "short_history",
    "low_base",
    "missing_source",
    "high_volatility",
    "low_liquidity",
    "volume_spike",
    "overbought",
    "oversold",
    "correction_disclosure",
    "search_spike",
    "risk_search",
    "hiring_decline",
    "no_active_postings",
    "signal_expired",
    "no_valuation_signal",
    "valuation_review_required",
]

_LATIN = re.compile(r"[A-Za-z]")


class RiskFlagCopyTest(unittest.TestCase):
    def test_every_analyzer_flag_maps_to_korean(self):
        for flag in _ALL_FLAGS:
            with self.subTest(flag=flag):
                text = _risk_flag_ko(flag)
                self.assertTrue(text)
                self.assertNotIn(flag, text)
                self.assertNotRegex(text, _LATIN)

    def test_review_required_prefix_is_expanded(self):
        text = _risk_flag_ko("review_required:correction")

        self.assertIn("정정공시", text)
        self.assertNotIn("review_required", text)

    def test_unknown_flag_never_leaks_the_identifier(self):
        text = _risk_flag_ko("some_new_flag_nobody_mapped")

        self.assertNotIn("some_new_flag", text)
        self.assertNotRegex(text, _LATIN)

    def test_evidence_list_translates_flags(self):
        items = _evidence_list(
            [{"source": "HIRING", "summary": "요약", "risk_flags": ["hiring_decline"]}]
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "hiring")
        self.assertNotIn("hiring_decline", items[0]["risk_flags"])
        self.assertIn("채용 공고가", items[0]["risk_flags"][0])


if __name__ == "__main__":
    unittest.main()
