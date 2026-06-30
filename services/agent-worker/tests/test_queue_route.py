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

from app.api.routes.queue import get_database_pool, get_task_handler_factory
from app.main import app


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.execute_results = ["UPDATE 1", "UPDATE 0"]

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [
            {
                "id": 50,
                "stock_id": 1,
                "stock_code": "005930",
                "task_type": "normalize_dart",
                "status": "pending",
                "priority": "batch",
                "source_raw_ids": [20],
                "source_signal_event_ids": None,
                "task_context": {"stock_code": "005930"},
                "retry_count": 0,
                "max_retry_count": 3,
                "error_message": None,
            }
        ]

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "WHERE id = $1" in sql:
            return {
                "id": args[0],
                "stock_id": 1,
                "task_type": "normalize_dart",
                "status": "retrying",
            }
        if "WITH next_task AS" in sql:
            if getattr(self, "claimed_once", False):
                return None
            self.claimed_once = True
            if args[0] == "normalize_report":
                return {"id": 50, "task_type": args[0], "status": "running"}
            return {
                "id": 51,
                "stock_id": 1,
                "task_type": args[0],
                "status": "running",
                "retry_count": 0,
                "max_retry_count": 0,
            }
        return {"id": 50, "task_type": args[0], "status": "running"}

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return self.execute_results.pop(0)


class FakeAcquire:
    def __init__(self, connection=None):
        self.connection = connection or FakeConnection()

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self, connection=None):
        self.connection = connection or FakeConnection()

    def acquire(self):
        return FakeAcquire(self.connection)


class QueueRouteTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_claim_next_task_returns_running_task(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

        response = client.post("/internal/queue/normalize_report/claim")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 50)
        self.assertEqual(response.json()["status"], "running")

    def test_sweep_stale_tasks_returns_cleanup_counts(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

        response = client.post(
            "/internal/queue/sweep-stale",
            json={"running_timeout_minutes": 30, "retrying_timeout_minutes": 120},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"retried_count": 1, "failed_count": 0})

    def test_list_tasks_returns_filtered_queue_items(self):
        connection = FakeConnection()
        app.dependency_overrides[get_database_pool] = lambda: FakePool(connection)
        client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

        response = client.get(
            "/internal/queue/tasks",
            params={"stock_code": "005930", "task_type": "normalize_dart", "status": "pending"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["items"][0]["task_type"], "normalize_dart")
        self.assertEqual(connection.calls[0][2], ("005930", "normalize_dart", "pending", 50))

    def test_retry_task_marks_task_retrying(self):
        connection = FakeConnection()
        app.dependency_overrides[get_database_pool] = lambda: FakePool(connection)
        client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

        response = client.post("/internal/queue/tasks/77/retry")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 77)
        self.assertEqual(response.json()["status"], "retrying")

    def test_run_batch_executes_until_idle_or_max_runs(self):
        async def handler(task):
            return {"handled": task["id"]}

        connection = FakeConnection()
        app.dependency_overrides[get_database_pool] = lambda: FakePool(connection)
        app.dependency_overrides[get_task_handler_factory] = lambda: lambda _: {"normalize_dart": handler}
        client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

        response = client.post("/internal/queue/normalize_dart/run-batch", json={"max_runs": 5})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["task_type"], "normalize_dart")
        self.assertEqual(payload["run_count"], 1)
        self.assertEqual(payload["results"][0]["task_id"], 51)
        self.assertFalse(payload["max_runs_reached"])


if __name__ == "__main__":
    unittest.main()
