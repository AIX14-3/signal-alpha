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

from app.api.routes.queue import get_database_pool
from app.main import app


class FakeConnection:
    async def fetchrow(self, sql, *args):
        return {"id": 50, "task_type": args[0], "status": "running"}


class FakeAcquire:
    async def __aenter__(self):
        return FakeConnection()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


class QueueRouteTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_claim_next_task_returns_running_task(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app)

        response = client.post("/internal/queue/normalize_report/claim")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 50)
        self.assertEqual(response.json()["status"], "running")


if __name__ == "__main__":
    unittest.main()
