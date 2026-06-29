"""Queue-wiring tests for the hiring OCR skill-enrichment step (ENRICH_HIRING).

Mirrors test_patent_enrich_task_handler — covers the two seams that put OCR
enrichment *inside* the queue pipeline:
  - HiringNormalizeTaskHandler enqueues ENRICH_HIRING (not ANALYZE_HIRING)
    after normalizing, carrying the just-normalized raw_ids.
  - HiringSkillEnrichTaskHandler enriches only those raw_ids (poster image → OCR →
    skill set cached on hiring_raw_details), then enqueues ANALYZE_HIRING — and
    degrades gracefully (skips OCR, still enqueues analysis) when no OCR processor.

DB is a recording fake; the OCR processor is injected.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.enrichment.hiring_skills import HiringSkillEnricher, image_urls_of
from app.orchestrator.alternative.tasks import (
    HiringNormalizeTaskHandler,
    HiringSkillEnrichTaskHandler,
)
from app.orchestrator.queue.task_types import ANALYZE_HIRING, ENRICH_HIRING


class FakeConnection:
    """Records SQL calls; returns canned hiring rows for fetch and ids for fetchval."""

    def __init__(self, hiring_rows=None):
        self.calls = []
        self.next_id = 900
        self.hiring_rows = hiring_rows if hiring_rows is not None else []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.hiring_rows

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
        types = set()
        for args in self.fetchval_args():
            for value in args:
                if value in (ENRICH_HIRING, ANALYZE_HIRING):
                    types.add(value)
        return types


class FakeOCR:
    def __init__(self, *, text="", error=None):
        self._text = text
        self._error = error
        self.calls = 0

    async def image_to_text(self, image_urls):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._text


_PENDING_ROW = {
    "raw_document_id": 10,
    "extra_payload": {"image_urls": ["https://cdn.example/poster.webp"]},
}


class HiringSkillEnrichTaskHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_enriches_scoped_raw_ids_then_enqueues_analysis(self):
        conn = FakeConnection(hiring_rows=[_PENDING_ROW])
        ocr = FakeOCR(text="백엔드: Python, Spring Boot / 인프라: Docker, Kubernetes, AWS")
        handler = HiringSkillEnrichTaskHandler(conn, ocr=ocr)

        result = await handler(
            {"id": 1, "stock_id": 5, "source_raw_ids": [10],
             "task_context": {"as_of": "2026-06-17"}}
        )

        self.assertEqual(result["enriched"]["success"], 1)
        self.assertFalse(result["ocr_skipped"])
        self.assertEqual(ocr.calls, 1)
        # cached the skills on hiring_raw_details …
        cache_writes = [c for c in conn.calls if c[0] == "execute" and "UPDATE hiring_raw_details" in c[1]]
        self.assertTrue(cache_writes)
        # … and handed off to analysis (not another enrich).
        self.assertEqual(conn.enqueued_task_types(), {ANALYZE_HIRING})
        self.assertTrue(result["analysis_task_ids"])

    async def test_no_ocr_skips_but_still_enqueues_analysis(self):
        conn = FakeConnection(hiring_rows=[_PENDING_ROW])
        handler = HiringSkillEnrichTaskHandler(conn, ocr_factory=lambda: None)

        result = await handler(
            {"id": 1, "stock_id": 5, "source_raw_ids": [10],
             "task_context": {"as_of": "2026-06-17"}}
        )

        self.assertTrue(result["ocr_skipped"])
        self.assertEqual(result["enriched"]["total"], 0)
        self.assertFalse(any("hiring_raw_details" in c[1] for c in conn.calls))
        self.assertEqual(conn.enqueued_task_types(), {ANALYZE_HIRING})

    async def test_ocr_failure_marks_failed_but_continues(self):
        conn = FakeConnection(hiring_rows=[_PENDING_ROW])
        ocr = FakeOCR(error=RuntimeError("tesseract missing"))
        handler = HiringSkillEnrichTaskHandler(conn, ocr=ocr)

        result = await handler(
            {"id": 1, "stock_id": 5, "source_raw_ids": [10],
             "task_context": {"as_of": "2026-06-17"}}
        )

        self.assertEqual(result["enriched"]["failed"], 1)
        self.assertEqual(conn.enqueued_task_types(), {ANALYZE_HIRING})


class HiringNormalizeFollowupTests(unittest.IsolatedAsyncioTestCase):
    async def test_followup_enqueues_enrich_hiring_with_raw_ids(self):
        conn = FakeConnection()
        handler = HiringNormalizeTaskHandler(conn)

        task_ids = await handler._enqueue_followups(
            stock_ids=[5, 5], raw_document_ids=[10, 11], as_of="2026-06-17"
        )

        self.assertTrue(task_ids)
        self.assertEqual(conn.enqueued_task_types(), {ENRICH_HIRING})
        self.assertTrue(any([10, 11] in list(args) for args in conn.fetchval_args()))

    async def test_followup_with_nothing_normalized_falls_back_to_analysis(self):
        conn = FakeConnection()
        handler = HiringNormalizeTaskHandler(conn)

        await handler._enqueue_followups(stock_ids=[5], raw_document_ids=[], as_of="2026-06-17")

        self.assertEqual(conn.enqueued_task_types(), {ANALYZE_HIRING})


class EnricherUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_skipped_when_no_image_urls(self):
        class Repo:
            def __init__(self):
                self.updates = []

            async def list_unenriched_hiring_details(self, *, limit, raw_document_ids):
                return [{"raw_document_id": 7, "extra_payload": {}}]

            async def update_hiring_ocr_skills(self, *, raw_document_id, skills, status):
                self.updates.append((raw_document_id, skills, status))

        repo = Repo()
        stats = await HiringSkillEnricher(repo, FakeOCR(text="Python"), raw_document_ids=[7]).run()
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(repo.updates, [(7, None, "skipped")])

    def test_image_urls_of_variants(self):
        self.assertEqual(image_urls_of({"image_urls": ["a", " b "]}), ["a", "b"])
        self.assertEqual(image_urls_of({"image_url": "solo"}), ["solo"])
        self.assertEqual(image_urls_of('{"image_urls": ["j"]}'), ["j"])
        self.assertEqual(image_urls_of({}), [])


if __name__ == "__main__":
    unittest.main()
