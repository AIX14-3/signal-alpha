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
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "WHERE id = $1" in sql:
            return {"id": args[0], "task_type": args[1], "retry_count": 0, "max_retry_count": 3}
        return {"id": 99, "task_type": args[0], "retry_count": 0, "max_retry_count": 3}

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return 77

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
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
        client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

        response = client.post("/internal/tasks/normalize_report/run")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["result"]["handled"], True)

    def test_run_task_executes_requested_task_id(self):
        from app.orchestrator.queue.tasks import QueueTaskRunner

        calls = []

        async def handler(task):
            calls.append(task["id"])
            return {"task_id": task["id"], "handled": True}

        async def run_test():
            connection = FakeConnection()
            runner = QueueTaskRunner(connection, {"collect_dart": handler})
            result = await runner.run_task("collect_dart", task_id=77)
            return connection, result

        import asyncio

        connection, result = asyncio.run(run_test())

        self.assertEqual(calls, [77])
        self.assertEqual(result["status"], "success")
        self.assertEqual(connection.calls[0][2], (77, "collect_dart"))

    def test_enqueue_task_uses_dedupe_by_default(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

        response = client.post(
            "/internal/tasks/collect_dart/enqueue",
            json={
                "stock_id": 1,
                "priority": "batch",
                "task_context": {
                    "stock_code": "005930",
                    "bgn_de": "20260601",
                    "end_de": "20260608",
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"task_id": 77, "task_type": "collect_dart"})


if __name__ == "__main__":
    unittest.main()
