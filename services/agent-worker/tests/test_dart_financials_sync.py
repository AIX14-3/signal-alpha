import unittest

from app.orchestrator.dart.financials_sync import DartFinancialsSyncService


class FakeCollector:
    def __init__(self, facts_by_key):
        self.facts_by_key = facts_by_key
        self.calls = []

    async def collect(self, *, stock_code, bsns_year, reprt_code):
        self.calls.append((bsns_year, reprt_code))
        return list(self.facts_by_key.get((bsns_year, reprt_code), []))


class FakeRepository:
    def __init__(self):
        self.batches = []

    async def upsert_facts(self, entries):
        self.batches.append(entries)
        return len(entries)


def _fact():
    return {
        "corp_code": "00126380",
        "rcept_no": "20260315000123",
        "bsns_year": 2024,
        "reprt_code": "11011",
        "fs_div": "CFS",
        "sj_div": "IS",
        "account_nm": "매출액",
    }


class DartFinancialsSyncServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_target_years_roll_back_from_prior_year(self):
        service = DartFinancialsSyncService(
            collector=FakeCollector({}),
            repository=FakeRepository(),
            lookback_years=3,
            current_year=2026,
        )
        # 직전 연도(2025)부터 3년 소급
        self.assertEqual(service._target_years(), [2025, 2024, 2023])

    async def test_sync_iterates_years_x_reprt_and_counts(self):
        collector = FakeCollector({(2025, "11011"): [_fact(), _fact()]})
        repository = FakeRepository()
        service = DartFinancialsSyncService(
            collector=collector,
            repository=repository,
            lookback_years=1,
            reprt_codes=("11011", "11014"),
            current_year=2026,
        )

        result = await service.sync_ticker(stock_code="005930", stock_id=7)

        # 1년 × 2 보고서 = 2회 호출
        self.assertEqual(collector.calls, [(2025, "11011"), (2025, "11014")])
        self.assertEqual(result["fetched_count"], 2)
        self.assertEqual(result["upserted_count"], 2)
        self.assertEqual(result["empty_periods"], ["2025/11014"])
        # stock_id 주입 확인
        self.assertEqual(repository.batches[0][0]["stock_id"], 7)

    async def test_sync_skips_empty_without_repository_call(self):
        collector = FakeCollector({})
        repository = FakeRepository()
        service = DartFinancialsSyncService(
            collector=collector,
            repository=repository,
            lookback_years=1,
            reprt_codes=("11011",),
            current_year=2026,
        )

        result = await service.sync_ticker(stock_code="005930")

        self.assertEqual(result["upserted_count"], 0)
        self.assertEqual(repository.batches, [])


if __name__ == "__main__":
    unittest.main()
