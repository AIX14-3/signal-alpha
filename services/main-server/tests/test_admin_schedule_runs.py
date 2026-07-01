import warnings
from datetime import UTC, datetime

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes import admin as admin_routes
from app.main import app


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "FROM collection_schedule_runs" in sql:
            return [
                {
                    "id": 123,
                    "schedule_id": 1,
                    "schedule_name": "daily-collection",
                    "trigger_reason": "manual",
                    "targets": '["dart"]',
                    "status": "ok",
                    "detail": '{"dart": 2}',
                    "started_at": datetime(2026, 7, 1, 4, 30, tzinfo=UTC),
                    "finished_at": datetime(2026, 7, 1, 4, 31, tzinfo=UTC),
                    "created_at": datetime(2026, 7, 1, 4, 30, tzinfo=UTC),
                }
            ]
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


def test_list_schedule_runs_returns_recent_history_rows():
    connection = FakeConnection()
    app.dependency_overrides[admin_routes.get_database_pool] = lambda: FakePool(connection)
    app.dependency_overrides[admin_routes.get_current_admin] = lambda: {
        "admin_id": 1,
        "admin_email": "admin@example.com",
    }
    try:
        client = TestClient(app)
        response = client.get("/api/admin/schedules/1/runs?limit=10")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {
                "id": 123,
                "schedule_id": 1,
                "schedule_name": "daily-collection",
                "trigger_reason": "manual",
                "targets": ["dart"],
                "status": "ok",
                "detail": {"dart": 2},
                "started_at": "2026-07-01T04:30:00+00:00",
                "finished_at": "2026-07-01T04:31:00+00:00",
                "created_at": "2026-07-01T04:30:00+00:00",
            }
        ]
    }
    assert connection.calls[0][2] == (1, 10)
