import sys
import unittest
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.schedules import get_database_pool
from app.main import app


class FakeConnection:
    def __init__(self):
        self.calls = []

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
        return [
            {"id": 1, "ticker": "005930"},
            {"id": 2, "ticker": "000660"},
        ]

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        # StockRepository.get_by_ticker
        return {"id": 1, "ticker": "005930"}

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "SELECT id" in sql:
            return None
        return 80 + args[0]


class FakeAcquire:
    def __init__(self, connection):
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self):
        self.connection = FakeConnection()

    def acquire(self):
        return FakeAcquire(self.connection)


class ReportScheduleRouteTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_report_collect_accepts_absolute_dates(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app)

        response = client.post(
            "/internal/schedules/report/collect",
            json={"date_start": "2025-01-01", "date_end": "2025-12-31", "max_pages": 100},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scheduled_count"], 2)

    def test_report_collect_rejects_bad_date(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app)

        response = client.post(
            "/internal/schedules/report/collect",
            json={"date_start": "2025/01/01"},
        )

        self.assertEqual(response.status_code, 422)

    def test_report_analyze_single_stock(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app)

        response = client.post(
            "/internal/schedules/report/analyze",
            json={"stock_code": "005930"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scheduled_count"], 1)

    def test_report_normalize_backfill_dry_run_only_lists_candidates(self):
        pool = FakePool()
        app.dependency_overrides[get_database_pool] = lambda: pool
        client = TestClient(app)

        response = client.post(
            "/internal/schedules/report/normalize-backfill",
            json={"limit": 2},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["candidate_count"], 2)
        self.assertEqual(body["scheduled_count"], 0)
        self.assertFalse(
            any("INSERT INTO processing_queue" in call[1] for call in pool.connection.calls)
        )

    def test_report_normalize_backfill_enqueues_normalize_report_when_enabled(self):
        pool = FakePool()
        app.dependency_overrides[get_database_pool] = lambda: pool
        client = TestClient(app)

        response = client.post(
            "/internal/schedules/report/normalize-backfill",
            json={"limit": 2, "dry_run": False},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["dry_run"])
        self.assertEqual(body["candidate_count"], 2)
        self.assertEqual(body["scheduled_count"], 2)
        enqueue_calls = [
            call for call in pool.connection.calls
            if call[0] == "fetchval" and "INSERT INTO processing_queue" in call[1]
        ]
        self.assertEqual(len(enqueue_calls), 2)
        self.assertTrue(all(call[2][1] == "normalize_report" for call in enqueue_calls))


if __name__ == "__main__":
    unittest.main()
