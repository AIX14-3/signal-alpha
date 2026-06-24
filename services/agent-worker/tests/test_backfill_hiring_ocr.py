"""Unit tests for the ENRICH_HIRING OCR backfill loop (script/backfill_hiring_ocr.py).

The batch loop is tested with a fake repository that drains its pending set as rows
are updated, plus a fake OCR — no DB, no real OCR. Covers: pending drain/termination,
image-less rows → skipped, and the no-progress guard (stale schema → no infinite loop).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_AW))
sys.path.insert(0, str(_AW / "script"))

from backfill_hiring_ocr import run_backfill  # noqa: E402


async def _nosleep(_seconds: float) -> None:
    return None


class FakeRepo:
    """pending 행을 보유하고, update 시 해당 행을 pending 집합에서 제거(실제 전이 모사)."""

    def __init__(self, rows: dict[int, dict], *, freeze: bool = False):
        # rows: {raw_document_id: extra_payload}
        self._pending = dict(rows)
        self._freeze = freeze  # True면 update가 no-op(스키마 미적용 모사)
        self.updates: list[tuple[int, str]] = []

    async def list_unenriched_hiring_details(self, *, limit, raw_document_ids=None):
        if raw_document_ids is not None:
            ids = [rid for rid in raw_document_ids if rid in self._pending][:limit]
        else:
            ids = list(self._pending.keys())[:limit]
        return [{"raw_document_id": rid, "extra_payload": self._pending[rid]} for rid in ids]

    async def update_hiring_ocr_skills(self, *, raw_document_id, skills, status):
        self.updates.append((raw_document_id, status))
        if not self._freeze:
            self._pending.pop(raw_document_id, None)


class FakeOCR:
    async def image_to_text(self, image_urls):
        return "Python, Spring Boot, Kubernetes"


class RunBackfillTests(unittest.IsolatedAsyncioTestCase):
    async def test_drains_pending_in_batches(self):
        rows = {i: {"image_urls": ["http://img/%d.webp" % i]} for i in range(250)}
        totals = await run_backfill(
            FakeRepo(rows), FakeOCR(), batch_size=100, sleep_s=0, sleeper=_nosleep
        )
        self.assertEqual(totals["batches"], 3)        # 100 + 100 + 50
        self.assertEqual(totals["total"], 250)
        self.assertEqual(totals["success"], 250)
        self.assertEqual(totals["skipped"], 0)

    async def test_image_less_rows_are_skipped(self):
        rows = {i: {} for i in range(5)}  # image_urls 없음 → skipped
        repo = FakeRepo(rows)
        totals = await run_backfill(repo, FakeOCR(), batch_size=10, sleep_s=0, sleeper=_nosleep)
        self.assertEqual(totals["skipped"], 5)
        self.assertEqual(totals["success"], 0)
        self.assertTrue(all(status == "skipped" for _, status in repo.updates))

    async def test_no_progress_guard_stops(self):
        # update가 no-op이면 행이 pending에 남아 무한루프가 될 수 있다 → 가드가 1배치 후 중단.
        rows = {i: {"image_urls": ["x"]} for i in range(10)}
        totals = await run_backfill(
            FakeRepo(rows, freeze=True), FakeOCR(),
            batch_size=5, sleep_s=0, sleeper=_nosleep,
        )
        self.assertEqual(totals["batches"], 1)

    async def test_max_batches_caps_run(self):
        rows = {i: {"image_urls": ["x"]} for i in range(1000)}
        totals = await run_backfill(
            FakeRepo(rows), FakeOCR(), batch_size=10, max_batches=3,
            sleep_s=0, sleeper=_nosleep,
        )
        self.assertEqual(totals["batches"], 3)
        self.assertEqual(totals["total"], 30)


if __name__ == "__main__":
    unittest.main()
