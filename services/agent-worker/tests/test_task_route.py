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

from app.api.routes.tasks import get_database_pool, get_task_handler_factory
from app.main import app


class FakeConnection:
    async def fetchrow(self, sql, *args):
        return {"id": 99, "task_type": args[0], "retry_count": 0, "max_retry_count": 3}

    async def execute(self, sql, *args):
        return "OK"


class FakeAcquire:
    async def __aenter__(self):
        return FakeConnection()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


class TaskRouteTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_run_next_task_executes_registered_handler(self):
        async def handler(task):
            return {"task_id": task["id"], "handled": True}

        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        app.dependency_overrides[get_task_handler_factory] = lambda: lambda connection: {"normalize_report": handler}
        client = TestClient(app)

        response = client.post("/internal/tasks/normalize_report/run")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["result"]["handled"], True)


if __name__ == "__main__":
    unittest.main()
