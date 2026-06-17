"""Queue-wiring tests for the patent C3 enrichment step.

Covers the two seams that put LLM enrichment *inside* the queue pipeline:
  - PatentNormalizeTaskHandler enqueues ENRICH_PATENT (not ANALYZE_ALTERNATIVE)
    after normalizing, carrying the just-normalized raw_ids.
  - PatentEnrichTaskHandler enriches only those raw_ids, then enqueues
    ANALYZE_ALTERNATIVE — and degrades gracefully (skips the LLM, still enqueues
    analysis) when no Gemini client is available.

DB is a recording fake; Gemini is injected.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.alternative.tasks import (
    PatentEnrichTaskHandler,
    PatentNormalizeTaskHandler,
)
from app.orchestrator.queue.task_types import ANALYZE_ALTERNATIVE, ENRICH_PATENT


class FakeConnection:
    """Records SQL calls; returns canned patent rows for fetch and ids for fetchval."""

    def __init__(self, patent_rows=None):
        self.calls = []
        self.next_id = 900
        self.patent_rows = patent_rows if patent_rows is not None else []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.patent_rows

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "FROM processing_queue" in sql:  # dedupe lookup → no existing task
            return None
        self.next_id += 1
        return self.next_id

    def fetchval_args(self):
        return [args for kind, _sql, args in self.calls if kind == "fetchval"]

    def enqueued_task_types(self):
        """Every task_type string that reached an enqueue (SELECT or INSERT)."""
        types = set()
        for args in self.fetchval_args():
            for value in args:
                if value in (ENRICH_PATENT, ANALYZE_ALTERNATIVE):
                    types.add(value)
        return types


class FakeClient:
    def __init__(self, *, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = 0

    async def generate_json(self, prompt):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


_PENDING_ROW = {
    "raw_document_id": 10,
    "application_no": "1020250200970",
    "patent_title": "이차전지 음극재",
    "extra_payload": {"astrtCont": "본 발명은 실리콘 음극재…"},
}


class PatentEnrichTaskHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_enriches_scoped_raw_ids_then_enqueues_analysis(self):
        conn = FakeConnection(patent_rows=[_PENDING_ROW])
        client = FakeClient(result={"significance": 0.7, "commercialization_stage": "development"})
        handler = PatentEnrichTaskHandler(conn, client=client)

        result = await handler(
            {
                "id": 1,
                "stock_id": 5,
                "source_raw_ids": [10],
                "task_context": {"as_of": "2026-06-17"},
            }
        )

        self.assertEqual(result["enriched"]["success"], 1)
        self.assertFalse(result["llm_skipped"])
        self.assertEqual(client.calls, 1)
        # cached the features …
        self.assertTrue(
            any("UPDATE patent_raw_details" in c[1] for c in conn.calls if c[0] == "execute")
        )
        # … and handed off to analysis (not another enrich).
        self.assertEqual(conn.enqueued_task_types(), {ANALYZE_ALTERNATIVE})
        self.assertTrue(result["analysis_task_ids"])

    async def test_no_client_skips_llm_but_still_enqueues_analysis(self):
        conn = FakeConnection(patent_rows=[_PENDING_ROW])
        handler = PatentEnrichTaskHandler(conn, client_factory=lambda: None)

        result = await handler(
            {
                "id": 1,
                "stock_id": 5,
                "source_raw_ids": [10],
                "task_context": {"as_of": "2026-06-17"},
            }
        )

        self.assertTrue(result["llm_skipped"])
        self.assertEqual(result["enriched"]["total"], 0)
        # No patent fetch / no cache write when enrichment is disabled …
        self.assertFalse(any("patent_raw_details" in c[1] for c in conn.calls))
        # … but the pipeline still moves on to analysis (count-based fallback).
        self.assertEqual(conn.enqueued_task_types(), {ANALYZE_ALTERNATIVE})


class PatentNormalizeFollowupTests(unittest.IsolatedAsyncioTestCase):
    async def test_followup_enqueues_enrich_patent_with_raw_ids(self):
        conn = FakeConnection()
        handler = PatentNormalizeTaskHandler(conn)

        task_ids = await handler._enqueue_followups(
            stock_ids=[5, 5], raw_document_ids=[10, 11], as_of="2026-06-17"
        )

        self.assertTrue(task_ids)
        self.assertEqual(conn.enqueued_task_types(), {ENRICH_PATENT})
        # raw_ids ride along so ENRICH enriches exactly what was normalized.
        self.assertTrue(any([10, 11] in list(args) for args in conn.fetchval_args()))

    async def test_followup_with_nothing_normalized_falls_back_to_analysis(self):
        conn = FakeConnection()
        handler = PatentNormalizeTaskHandler(conn)

        await handler._enqueue_followups(stock_ids=[5], raw_document_ids=[], as_of="2026-06-17")

        self.assertEqual(conn.enqueued_task_types(), {ANALYZE_ALTERNATIVE})


if __name__ == "__main__":
    unittest.main()
