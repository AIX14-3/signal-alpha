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

from app.api.routes.price import get_database_pool
from app.main import app


def ohlcv_row(day, close):
    return {
        "trade_date": date(2026, 3, day),
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "volume": 1_000_000,
        "foreign_net": 100,
        "institution_net": 50,
        "change_pct": None,
        "market_cap": None,
    }


class FakeConnection:
    async def fetchrow(self, sql, *args):
        return {"id": 7, "ticker": args[0], "name": "삼성전자"}

    async def fetch(self, sql, *args):
        return [ohlcv_row(day, 100 + day) for day in range(1, 31)]


class FakeAcquire:
    async def __aenter__(self):
        return FakeConnection()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


class PriceRouteTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_analyze_returns_price_source_result(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

        response = client.post("/internal/price/analyze/005930")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source"], "PRICE")
        self.assertEqual(body["stock_code"], "005930")
        self.assertIn(body["direction"], ["positive", "neutral", "negative", "mixed", "unknown"])
        self.assertIsInstance(body["score"], float)
        self.assertIn("risk_flags", body)

    def test_analyze_without_database_pool_is_503(self):
        client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

        response = client.post("/internal/price/analyze/005930")

        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
