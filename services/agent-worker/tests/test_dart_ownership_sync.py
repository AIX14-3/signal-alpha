import unittest
from datetime import date

from app.orchestrator.dart.ownership_sync import DartOwnershipSyncService


class FakeCollector:
    def __init__(self, events):
        self.events = events
        self.calls = []

    async def collect(self, *, stock_code):
        self.calls.append(stock_code)
        return [dict(event) for event in self.events]

    async def fetch_trade_detail(self, *, rcept_no):
        return "onmarket_buy", None


class FakeRepository:
    def __init__(self):
        self.batches = []

    async def upsert_events(self, entries):
        self.batches.append(entries)
        # 실 리포지토리처럼 필수키(holder_name) 누락 행은 폐기한 수를 반환.
        return sum(1 for entry in entries if entry.get("holder_name"))

    async def list_unenriched_ownership(self, *, stock_id, limit):
        return []  # enrich 대상 없음(이 테스트는 수집/카운트 검증)

    async def update_trade_detail(self, *, corp_code, rcept_no, trade_type, unit_price):
        pass


def _event(**overrides):
    base = {
        "corp_code": "00126380",
        "rcept_no": "20260315000123",
        "report_date": date(2026, 3, 15),
        "holder_name": "국민연금공단",
        "holder_type": "major",
    }
    base.update(overrides)
    return base


class DartOwnershipSyncServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_sync_counts_and_holder_type_breakdown(self):
        collector = FakeCollector(
            [_event(), _event(holder_type="executive", holder_name="홍길동")]
        )
        repository = FakeRepository()
        service = DartOwnershipSyncService(collector=collector, repository=repository)

        result = await service.sync_ticker(stock_code="005930", stock_id=7)

        self.assertEqual(collector.calls, ["005930"])
        self.assertEqual(result["fetched_count"], 2)
        self.assertEqual(result["upserted_count"], 2)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(result["by_holder_type"], {"major": 1, "executive": 1})
        # stock_id 주입 확인
        self.assertEqual(repository.batches[0][0]["stock_id"], 7)

    async def test_skipped_count_surfaces_filtered_rows(self):
        collector = FakeCollector([_event(), _event(holder_name=None)])
        repository = FakeRepository()
        service = DartOwnershipSyncService(collector=collector, repository=repository)

        result = await service.sync_ticker(stock_code="005930")

        self.assertEqual(result["fetched_count"], 2)
        self.assertEqual(result["upserted_count"], 1)
        self.assertEqual(result["skipped_count"], 1)

    async def test_sync_skips_empty_without_repository_call(self):
        collector = FakeCollector([])
        repository = FakeRepository()
        service = DartOwnershipSyncService(collector=collector, repository=repository)

        result = await service.sync_ticker(stock_code="005930")

        self.assertEqual(result["fetched_count"], 0)
        self.assertEqual(result["upserted_count"], 0)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(result["by_holder_type"], {})
        self.assertEqual(repository.batches, [])


if __name__ == "__main__":
    unittest.main()
