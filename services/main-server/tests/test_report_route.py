"""Unit tests for the report route's source-block mapping.

Locks the C안 Phase 2 contract: the worker's ``score_breakdown`` exposes the
alternative sources as flat top-level keys (HIRING/DATALAB), not nested under a
single ALTERNATIVE umbrella. The report page (the live frontend consumer) reads
them through ``_source_block`` keyed by ``_SOURCE_TO_BREAKDOWN``.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.api.routes.reports import _SOURCE_TO_BREAKDOWN, _source_block


_BREAKDOWN = {
    "DART": {"direction": "neutral", "score_100": 50.0, "data_status": "ok", "summary": "공시 중립"},
    "PRICE": {"direction": "positive", "score_100": 72.0, "data_status": "ok"},
    "HIRING": {"direction": "positive", "score_100": 64.0, "data_status": "ok", "summary": "채용 증가"},
    "DATALAB": {"direction": "negative", "score_100": 30.0, "data_status": "ok"},
}


class SourceBlockMappingTest(unittest.TestCase):
    def test_alt_sources_map_to_their_own_flat_keys(self):
        # 과거엔 둘 다 ALTERNATIVE 로 묶였다 → 이제 각자 자기 키.
        self.assertEqual(_SOURCE_TO_BREAKDOWN["hiring"], "HIRING")
        self.assertEqual(_SOURCE_TO_BREAKDOWN["datalab"], "DATALAB")

    def test_reads_flat_hiring_and_datalab_blocks(self):
        hiring = _source_block("hiring", _BREAKDOWN, locked=False)
        self.assertEqual(hiring["direction"], "positive")
        self.assertEqual(hiring["score"], 64)
        self.assertEqual(hiring["data_status"], "ok")
        self.assertEqual(hiring["summary"], "채용 증가")

        datalab = _source_block("datalab", _BREAKDOWN, locked=False)
        self.assertEqual(datalab["direction"], "negative")
        self.assertEqual(datalab["score"], 30)

    def test_price_and_dart_unchanged(self):
        self.assertEqual(_source_block("price", _BREAKDOWN, locked=False)["score"], 72)
        self.assertEqual(_source_block("dart", _BREAKDOWN, locked=False)["score"], 50)

    def test_absent_source_is_missing(self):
        block = _source_block("hiring", {"DART": _BREAKDOWN["DART"]}, locked=False)
        self.assertEqual(block["direction"], "unknown")
        self.assertEqual(block["data_status"], "missing")
        self.assertIsNone(block["score"])

    def test_locked_block_hides_detail(self):
        block = _source_block("hiring", _BREAKDOWN, locked=True)
        self.assertEqual(block, {"source": "hiring", "locked": True})


if __name__ == "__main__":
    unittest.main()
