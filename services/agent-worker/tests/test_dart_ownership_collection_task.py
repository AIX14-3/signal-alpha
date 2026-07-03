import unittest
from datetime import date

from app.orchestrator.dart.tasks import DartOwnershipCollectionTaskHandler


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


class FakeOwnershipRepository:
    def __init__(self):
        self.entries = []

    async def upsert_events(self, entries):
        self.entries.extend(entries)
        return len(entries)


class DartOwnershipCollectionTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_collects_and_upserts_ownership_events_with_stock_id(self):
        collector = FakeOwnershipCollector()
        repository = FakeOwnershipRepository()
        handler = DartOwnershipCollectionTaskHandler(
            connection=None,
            settings=object(),
            collector=collector,
            repository=repository,
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


if __name__ == "__main__":
    unittest.main()
