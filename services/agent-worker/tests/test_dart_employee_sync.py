import unittest

from app.orchestrator.dart.employee_sync import DartEmployeeSyncService


class FakeCollector:
    """(bsns_year, reprt_code) → stats 리스트 매핑. 없는 키는 빈 리스트(무자료)."""

    def __init__(self, by_period):
        self.by_period = by_period
        self.calls = []

    async def collect(self, *, stock_code, bsns_year, reprt_code):
        self.calls.append((stock_code, bsns_year, reprt_code))
        return [dict(s) for s in self.by_period.get((bsns_year, reprt_code), [])]


class FakeRepository:
    def __init__(self):
        self.batches = []

    async def upsert_stats(self, entries):
        self.batches.append(entries)
        # 실 리포지토리처럼 필수키(rcept_no) 누락 행은 폐기한 수를 반환.
        return sum(1 for entry in entries if entry.get("rcept_no"))


def _stat(**overrides):
    base = {
        "corp_code": "00126380",
        "rcept_no": "20250311001085",
        "bsns_year": 2024,
        "reprt_code": "11011",
        "segment": "DX",
        "sex": "남",
        "headcount": 38291,
    }
    base.update(overrides)
    return base


class DartEmployeeSyncServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_sync_loops_years_and_reprt_codes(self):
        collector = FakeCollector({(2024, "11011"): [_stat(), _stat(sex="여")]})
        repository = FakeRepository()
        service = DartEmployeeSyncService(
            collector=collector,
            repository=repository,
            lookback_years=2,
            reprt_codes=("11011", "11012"),
            current_year=2024,
        )

        result = await service.sync_ticker(stock_code="005930", stock_id=7)

        # 2개 연도(2024, 2023) × 2개 reprt = 4회 호출
        self.assertEqual(len(collector.calls), 4)
        self.assertEqual(result["years"], [2024, 2023])
        self.assertEqual(result["fetched_count"], 2)
        self.assertEqual(result["upserted_count"], 2)
        self.assertEqual(result["skipped_count"], 0)
        # 자료 없는 기간은 empty_periods 로 가시화
        self.assertIn("2023/11011", result["empty_periods"])
        # stock_id 주입 확인
        self.assertEqual(repository.batches[0][0]["stock_id"], 7)

    async def test_skipped_count_surfaces_filtered_rows(self):
        collector = FakeCollector({(2024, "11011"): [_stat(), _stat(rcept_no="")]})
        repository = FakeRepository()
        service = DartEmployeeSyncService(
            collector=collector,
            repository=repository,
            lookback_years=1,
            reprt_codes=("11011",),
            current_year=2024,
        )

        result = await service.sync_ticker(stock_code="005930")

        self.assertEqual(result["fetched_count"], 2)
        self.assertEqual(result["upserted_count"], 1)
        self.assertEqual(result["skipped_count"], 1)

    async def test_all_empty_skips_repository(self):
        collector = FakeCollector({})
        repository = FakeRepository()
        service = DartEmployeeSyncService(
            collector=collector,
            repository=repository,
            lookback_years=1,
            reprt_codes=("11011",),
            current_year=2024,
        )

        result = await service.sync_ticker(stock_code="005930")

        self.assertEqual(result["fetched_count"], 0)
        self.assertEqual(result["upserted_count"], 0)
        self.assertEqual(result["empty_periods"], ["2024/11011"])
        self.assertEqual(repository.batches, [])


if __name__ == "__main__":
    unittest.main()
