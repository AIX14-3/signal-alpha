import unittest

from app.orchestrator.queue.legacy_cleanup import (
    LEGACY_DART_BACKFILL_TASK_TYPE,
    cleanup_legacy_dart_backfill_tasks,
)


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.fetch_results = [
            [
                {"status": "pending", "task_count": 2},
                {"status": "retrying", "task_count": 1},
            ]
        ]
        self.updated_count = 3

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.fetch_results.pop(0)

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self.updated_count


class LegacyQueueCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_reports_legacy_dart_backfill_tasks_without_update(self):
        connection = FakeConnection()

        summary = await cleanup_legacy_dart_backfill_tasks(connection, execute=False)

        self.assertEqual(summary["task_type"], LEGACY_DART_BACKFILL_TASK_TYPE)
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["matched_count"], 3)
        self.assertEqual(summary["updated_count"], 0)
        self.assertEqual(summary["by_status"], {"pending": 2, "retrying": 1})
        self.assertEqual([call[0] for call in connection.calls], ["fetch"])

    async def test_execute_marks_legacy_dart_backfill_tasks_skipped(self):
        connection = FakeConnection()

        summary = await cleanup_legacy_dart_backfill_tasks(
            connection,
            execute=True,
            limit=100,
        )

        self.assertFalse(summary["dry_run"])
        self.assertEqual(summary["matched_count"], 3)
        self.assertEqual(summary["updated_count"], 3)
        self.assertEqual([call[0] for call in connection.calls], ["fetch", "fetchval"])
        self.assertEqual(connection.calls[1][2][0], LEGACY_DART_BACKFILL_TASK_TYPE)
        self.assertIn("legacy", connection.calls[1][2][2])
        self.assertEqual(connection.calls[1][2][3], 100)
