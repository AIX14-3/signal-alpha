import sys
import unittest
import warnings
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.dart import get_database_pool
from app.main import app


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [
            {
                "id": 10,
                "stock_id": 1,
                "stock_code": "005930",
                "stock_name": "Samsung Electronics",
                "analysis_date": date(2026, 6, 8),
                "run_key": "DART",
                "analysis_mode": "dart_only",
                "version": "1.0",
                "source_signal_event_ids": [501],
                "base_score": 62,
                "warning": None,
                "agent_results": [
                    {
                        "debate_method": "D-1",
                        "method_signal": "positive",
                    }
                ],
                "signal_events": [
                    {
                        "id": 501,
                        "title": "Quarterly report",
                    }
                ],
            }
        ]


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return FakeAcquire(self.connection)


class DartRouteTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_list_analysis_results_filters_by_stock_and_date(self):
        connection = FakeConnection()
        app.dependency_overrides[get_database_pool] = lambda: FakePool(connection)
        client = TestClient(app)

        response = client.get(
            "/internal/dart/analysis-results",
            params={"stock_code": "005930", "analysis_date": "2026-06-08"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["items"][0]["stock_code"], "005930")
        self.assertEqual(response.json()["items"][0]["analysis_date"], "2026-06-08")
        self.assertEqual(response.json()["items"][0]["agent_results"][0]["method_signal"], "positive")
        self.assertEqual(connection.calls[0][2], ("005930", date(2026, 6, 8), 20))


if __name__ == "__main__":
    unittest.main()
