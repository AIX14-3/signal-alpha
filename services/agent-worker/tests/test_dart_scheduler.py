import unittest

from app.orchestrator.dart.scheduler import DartCollectionScheduler


class FakeStockRepository:
    def __init__(self):
        self.calls = []

    async def list_active(self, limit=100):
        self.calls.append(limit)
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


class DartCollectionSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_due_collections_for_active_stocks(self):
        stock_repository = FakeStockRepository()
        queue_repository = FakeQueueRepository()
        scheduler = DartCollectionScheduler(
            stock_repository=stock_repository,
            queue_repository=queue_repository,
        )

        result = await scheduler.enqueue_due_collections(
            limit=2,
            end_de="20260610",
            priority="batch",
        )

        self.assertEqual(result, {"scheduled_count": 2, "task_ids": [101, 102]})
        self.assertEqual(stock_repository.calls, [2])
        self.assertEqual(queue_repository.calls[0]["stock_id"], 1)
        self.assertEqual(queue_repository.calls[0]["task_type"], "collect_dart")
        self.assertEqual(queue_repository.calls[0]["task_context"], {"stock_code": "005930", "end_de": "20260610"})
        self.assertTrue(queue_repository.calls[0]["dedupe"])
        self.assertEqual(queue_repository.calls[1]["task_context"]["stock_code"], "000660")


if __name__ == "__main__":
    unittest.main()
