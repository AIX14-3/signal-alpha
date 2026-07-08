import unittest
from datetime import date

from app.orchestrator.dart.tasks import DartOwnershipCollectionTaskHandler
from app.orchestrator.queue.task_types import NORMALIZE_DART_OWNERSHIP


class FakeOwnershipCollector:
    def __init__(self):
        self.calls = []

    async def collect(self, *, stock_code):
        self.calls.append(stock_code)
        return [
            {
                "corp_code": "00126380",
                "rcept_no": "20260101000001",
                "line_seq": 0,
                "report_date": date(2026, 1, 1),
                "holder_name": "홍길동",
                "holder_type": "executive",
                "shares": 10,
                "ratio": 0.01,
                "shares_delta": 10,
                "ratio_delta": 0.01,
                "report_reason": "대표이사",
            }
        ]

    async def fetch_trade_detail(self, *, rcept_no):
        return "onmarket_buy", None


class FakeOwnershipRepository:
    def __init__(self):
        self.entries = []

    async def upsert_events(self, entries):
        self.entries.extend(entries)
        return len(entries)

    async def list_unenriched_ownership(self, *, stock_id, limit):
        return []

    async def update_trade_detail(self, *, corp_code, rcept_no, trade_type, unit_price):
        pass


class FakeQueueRepository:
    def __init__(self):
        self.calls = []

    async def enqueue(self, **kwargs):
        self.calls.append(kwargs)
        return 301


class DartOwnershipCollectionTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_collects_upserts_and_enqueues_ownership_normalization(self):
        collector = FakeOwnershipCollector()
        repository = FakeOwnershipRepository()
        queue = FakeQueueRepository()
        handler = DartOwnershipCollectionTaskHandler(
            connection=None,
            settings=object(),
            collector=collector,
            repository=repository,
            queue_repository=queue,
        )

        result = await handler(
            {
                "stock_id": 7,
                "task_context": {"stock_code": "005930"},
            }
        )

        self.assertEqual(collector.calls, ["005930"])
        self.assertEqual(repository.entries[0]["stock_id"], 7)
        self.assertEqual(result["ticker"], "005930")
        self.assertEqual(result["fetched_count"], 1)
        self.assertEqual(result["upserted_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(result["by_holder_type"], {"executive": 1})
        self.assertEqual(result["normalize_task_id"], 301)
        self.assertEqual(queue.calls[0]["stock_id"], 7)
        self.assertEqual(queue.calls[0]["task_type"], NORMALIZE_DART_OWNERSHIP)
        self.assertEqual(queue.calls[0]["task_context"], {"stock_code": "005930"})
        self.assertTrue(queue.calls[0]["dedupe"])


if __name__ == "__main__":
    unittest.main()
