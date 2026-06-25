import unittest
import warnings
from datetime import UTC, datetime

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.analytics import get_database_pool
from app.main import app


def _task(task_type, status, minute):
    return {
        "id": minute,
        "task_type": task_type,
        "status": status,
        "created_at": datetime(2026, 6, 23, 10, minute, tzinfo=UTC),
        "updated_at": datetime(2026, 6, 23, 10, minute, tzinfo=UTC),
        "stock_code": "005930",
    }


class FakeConnection:
    def __init__(self, tasks):
        self.tasks = tasks

    async def fetch(self, sql, *args):
        if "FROM api.analysis_pipeline_status" in sql:
            # list_tasks는 created_at DESC 정렬을 가정.
            return sorted(self.tasks, key=lambda t: t["created_at"], reverse=True)
        raise AssertionError(f"Unexpected fetch SQL: {sql}")


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


class AnalyticsRoutesTest(unittest.TestCase):
    def _client(self, tasks):
        app.dependency_overrides[get_database_pool] = lambda: FakePool(FakeConnection(tasks))
        return TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_no_tasks_is_pending(self):
        response = self._client([]).get("/api/analytics/005930/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["overall"], "pending")
        self.assertEqual(response.json()["stages"], [])

    def test_all_success_is_success(self):
        tasks = [_task("normalize", "success", 1), _task("score", "success", 2)]
        response = self._client(tasks).get("/api/analytics/005930/status")
        self.assertEqual(response.json()["overall"], "success")
        self.assertEqual(len(response.json()["stages"]), 2)

    def test_running_task_is_running(self):
        tasks = [_task("normalize", "success", 1), _task("score", "running", 2)]
        response = self._client(tasks).get("/api/analytics/005930/status")
        self.assertEqual(response.json()["overall"], "running")

    def test_latest_status_per_task_type_wins(self):
        tasks = [_task("score", "failed", 1), _task("score", "success", 5)]
        response = self._client(tasks).get("/api/analytics/005930/status")
        # task_type별 최신(분 5, success)만 반영 → success.
        self.assertEqual(response.json()["overall"], "success")
        self.assertEqual(len(response.json()["stages"]), 1)


if __name__ == "__main__":
    unittest.main()
