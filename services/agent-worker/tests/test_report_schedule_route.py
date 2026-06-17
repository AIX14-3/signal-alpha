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
    async def fetch(self, sql, *args):
        return [
            {"id": 1, "ticker": "005930"},
            {"id": 2, "ticker": "000660"},
        ]

    async def fetchrow(self, sql, *args):
        # StockRepository.get_by_ticker
        return {"id": 1, "ticker": "005930"}

    async def fetchval(self, sql, *args):
        if "SELECT id" in sql:
            return None
        return 80 + args[0]


class FakeAcquire:
    async def __aenter__(self):
        return FakeConnection()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


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


if __name__ == "__main__":
    unittest.main()
