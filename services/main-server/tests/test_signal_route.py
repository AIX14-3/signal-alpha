import unittest
import warnings
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.signals import get_database_pool
from app.main import app


class FakeConnection:
    async def fetchrow(self, sql, *args):
        return {
            "id": 7,
            "ticker": args[0],
            "name": "삼성전자",
            "signal": "neutral",
            "summary": "중립 신호",
        }


class FakeAcquire:
    async def __aenter__(self):
        return FakeConnection()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


class SignalRouteTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_get_signal_by_ticker_returns_current_signal(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app)

        response = client.get("/signals/005930")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ticker"], "005930")
        self.assertEqual(response.json()["signal"], "neutral")


if __name__ == "__main__":
    unittest.main()
