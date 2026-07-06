import unittest

from app.orchestrator.report.normalize_backfill import schedule_report_normalize_backfill


class FakeConnection:
    def __init__(self, *, existing_task_ids_by_raw_id=None, stock_row=None):
        self.calls = []
        self.existing_task_ids_by_raw_id = existing_task_ids_by_raw_id or {}
        self.stock_row = stock_row

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "FROM report_raw_details" in sql:
            return [
                {
                    "raw_document_id": 101,
                    "stock_id": 1,
                    "stock_code": "005930",
                    "title": "Report A",
                    "securities_firm": "Firm A",
                    "publish_date": "2026-06-20",
                },
                {
                    "raw_document_id": 102,
                    "stock_id": 2,
                    "stock_code": "000660",
                    "title": "Report B",
                    "securities_firm": "Firm B",
                    "publish_date": "2026-06-19",
                },
            ]
        return []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.stock_row

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "SELECT id" in sql:
            source_raw_ids = args[3] if len(args) > 3 else None
            if source_raw_ids:
                existing_id = self.existing_task_ids_by_raw_id.get(source_raw_ids[0])
                if existing_id is not None:
                    return existing_id
            return None
        return 1000 + int(args[0])


class ReportNormalizeBackfillOpsTest(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_lists_candidates_without_enqueueing(self):
        connection = FakeConnection()

        summary = await schedule_report_normalize_backfill(
            connection,
            limit=2,
            dry_run=True,
        )

        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["candidate_count"], 2)
        self.assertEqual(summary["scheduled_count"], 0)
        self.assertEqual(summary["enqueued_count"], 0)
        self.assertEqual(summary["reused_count"], 0)
        self.assertEqual([candidate["raw_document_id"] for candidate in summary["candidates"]], [101, 102])
        self.assertFalse(
            any("INSERT INTO processing_queue" in call[1] for call in connection.calls)
        )

    async def test_execute_enqueues_missing_normalize_tasks_and_reuses_open_ones(self):
        connection = FakeConnection(existing_task_ids_by_raw_id={101: 501})

        summary = await schedule_report_normalize_backfill(
            connection,
            limit=2,
            dry_run=False,
            priority="immediate",
        )

        self.assertFalse(summary["dry_run"])
        self.assertEqual(summary["candidate_count"], 2)
        self.assertEqual(summary["scheduled_count"], 2)
        self.assertEqual(summary["task_ids"], [501, 1002])
        self.assertEqual(summary["enqueued_count"], 1)
        self.assertEqual(summary["reused_count"], 1)
        enqueue_calls = [
            call
            for call in connection.calls
            if call[0] == "fetchval" and "INSERT INTO processing_queue" in call[1]
        ]
        self.assertEqual(len(enqueue_calls), 1)
        self.assertEqual(enqueue_calls[0][2][1], "normalize_report")
        self.assertEqual(enqueue_calls[0][2][2], "immediate")
        self.assertEqual(enqueue_calls[0][2][3], [102])

    async def test_unknown_stock_code_returns_empty_summary(self):
        connection = FakeConnection(stock_row=None)

        summary = await schedule_report_normalize_backfill(
            connection,
            stock_code="999999",
            dry_run=False,
        )

        self.assertEqual(summary["candidate_count"], 0)
        self.assertEqual(summary["scheduled_count"], 0)
        self.assertEqual(summary["task_ids"], [])
        self.assertFalse(any(call[0] == "fetch" for call in connection.calls))
