import unittest

from app.orchestrator.dart.tasks import (
    DartEmployeeCollectionTaskHandler,
    DartFinancialsCollectionTaskHandler,
)


class FakeFinancialsCollector:
    def __init__(self):
        self.calls = []

    async def collect(self, *, stock_code, bsns_year, reprt_code):
        self.calls.append((stock_code, bsns_year, reprt_code))
        return [
            {
                "corp_code": "00126380",
                "rcept_no": f"{bsns_year}{reprt_code}",
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
                "fs_div": "CFS",
                "sj_div": "BS",
                "account_nm": "assets_total",
                "amount_krw": 100,
            }
        ]


class FakeFinancialFactsRepository:
    def __init__(self):
        self.entries = []

    async def upsert_facts(self, entries):
        self.entries.extend(entries)
        return len(entries)


class FakeEmployeeCollector:
    def __init__(self):
        self.calls = []

    async def collect(self, *, stock_code, bsns_year, reprt_code):
        self.calls.append((stock_code, bsns_year, reprt_code))
        return [
            {
                "corp_code": "00126380",
                "rcept_no": f"{bsns_year}{reprt_code}",
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
                "line_seq": 0,
                "segment": "semiconductor",
                "sex": "M",
                "headcount": 10,
            }
        ]


class FakeEmployeeStatsRepository:
    def __init__(self):
        self.entries = []

    async def upsert_stats(self, entries):
        self.entries.extend(entries)
        return len(entries)


class _Settings:
    dart_financials_lookback_years = 1
    dart_financials_reprt_codes = ["11011"]
    dart_employee_lookback_years = 1
    dart_employee_reprt_codes = ["11011"]


class DartStructuredCollectionTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_financials_handler_syncs_ticker_with_task_stock_id(self):
        collector = FakeFinancialsCollector()
        repository = FakeFinancialFactsRepository()
        handler = DartFinancialsCollectionTaskHandler(
            connection=None,
            settings=_Settings(),
            collector=collector,
            repository=repository,
            current_year=2026,
        )

        result = await handler(
            {
                "stock_id": 7,
                "task_context": {"stock_code": "005930"},
            }
        )

        self.assertEqual(collector.calls, [("005930", 2026, "11011")])
        self.assertEqual(repository.entries[0]["stock_id"], 7)
        self.assertEqual(result["ticker"], "005930")
        self.assertEqual(result["years"], [2026])
        self.assertEqual(result["reprt_codes"], ["11011"])
        self.assertEqual(result["fetched_count"], 1)
        self.assertEqual(result["upserted_count"], 1)
        self.assertEqual(result["skipped_count"], 0)

    async def test_employee_handler_syncs_ticker_with_task_stock_id(self):
        collector = FakeEmployeeCollector()
        repository = FakeEmployeeStatsRepository()
        handler = DartEmployeeCollectionTaskHandler(
            connection=None,
            settings=_Settings(),
            collector=collector,
            repository=repository,
            current_year=2026,
        )

        result = await handler(
            {
                "stock_id": 7,
                "task_context": {"stock_code": "005930"},
            }
        )

        self.assertEqual(collector.calls, [("005930", 2026, "11011")])
        self.assertEqual(repository.entries[0]["stock_id"], 7)
        self.assertEqual(result["ticker"], "005930")
        self.assertEqual(result["years"], [2026])
        self.assertEqual(result["reprt_codes"], ["11011"])
        self.assertEqual(result["fetched_count"], 1)
        self.assertEqual(result["upserted_count"], 1)
        self.assertEqual(result["skipped_count"], 0)


if __name__ == "__main__":
    unittest.main()
