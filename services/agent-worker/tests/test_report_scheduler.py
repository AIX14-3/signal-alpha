import unittest

from app.orchestrator.report.scheduler import ReportCollectionScheduler


class FakeStockRepository:
    async def list_active(self, limit=100):
        return [
            {"id": 1, "ticker": "005930"},
            {"id": 2, "ticker": "000660"},
        ]


class FakeQueueRepository:
    def __init__(self):
        self.calls = []

    async def enqueue(self, **kwargs):
        self.calls.append(kwargs)
        return 100 + len(self.calls)


class ReportCollectionSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_absolute_dates_are_passed_in_task_context(self):
        queue = FakeQueueRepository()
        scheduler = ReportCollectionScheduler(
            stock_repository=FakeStockRepository(),
            queue_repository=queue,
        )

        result = await scheduler.enqueue_due_collections(
            limit=2,
            date_start="2025-01-01",
            date_end="2025-12-31",
            max_pages=100,
            priority="batch",
        )

        self.assertEqual(result, {"scheduled_count": 2, "task_ids": [101, 102]})
        ctx = queue.calls[0]["task_context"]
        self.assertEqual(ctx["stock_code"], "005930")
        self.assertEqual(ctx["date_start"], "2025-01-01")
        self.assertEqual(ctx["date_end"], "2025-12-31")
        self.assertEqual(ctx["max_pages"], 100)
        self.assertEqual(queue.calls[0]["task_type"], "collect_report")
        self.assertTrue(queue.calls[0]["dedupe"])

    async def test_days_back_fallback_omits_date_keys(self):
        queue = FakeQueueRepository()
        scheduler = ReportCollectionScheduler(
            stock_repository=FakeStockRepository(),
            queue_repository=queue,
        )

        await scheduler.enqueue_due_collections(days_back=7)

        ctx = queue.calls[0]["task_context"]
        self.assertEqual(ctx["days_back"], 7)
        self.assertNotIn("date_start", ctx)
        self.assertNotIn("date_end", ctx)


if __name__ == "__main__":
    unittest.main()
