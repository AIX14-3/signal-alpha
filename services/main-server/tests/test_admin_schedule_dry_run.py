import warnings
from datetime import UTC, datetime, time

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes import admin as admin_routes
from app.main import app


class FakeConnection:
    async def fetchrow(self, sql, *args):
        if "FROM collection_schedules" in sql:
            return {
                "id": args[0],
                "name": "report-collection",
                "enabled": True,
                "run_at_local": time(6, 0),
                "timezone": "Asia/Seoul",
                "targets": ["report"],
                "dart_limit": 10,
                "price_modes": ["snapshot"],
                "report_limit": 50,
                "report_days_back": 5,
                "report_max_pages": 12,
                "alternative_collect_enabled": True,
                "alternative_analyze_enabled": True,
                "alternative_collect_timeout_seconds": 900,
                "alternative_analyze_timeout_seconds": 1200,
                "backpressure_max_waiting": 20,
                "backpressure_max_failed": 3,
                "frequency_minutes": 720,
                "active_from_local": time(6, 0),
                "active_until_local": time(18, 0),
                "last_run_at": None,
                "last_status": None,
                "last_detail": None,
                "next_run_at": None,
                "manual_trigger_requested_at": None,
                "updated_by": None,
                "updated_at": datetime(2026, 7, 1, 1, 0, tzinfo=UTC),
            }
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")


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


def test_admin_schedule_dry_run_proxies_schedule_policy_to_worker(monkeypatch):
    calls = []

    async def fake_worker_request(method, path, *, settings, json_body=None, params=None):
        calls.append((method, path, json_body, params))
        return {
            "would_fire": False,
            "decision": {
                "agent": "scheduler",
                "policy": "scheduler-agent-v1",
                "action": "skip",
                "reason": "not-due",
                "schedule_id": 1,
                "schedule_name": "report-collection",
                "targets": ["report"],
            },
            "next_run_at": "2026-07-01T18:00:00+09:00",
            "backpressure": {"reason": None},
        }

    app.dependency_overrides[admin_routes.get_database_pool] = lambda: FakePool(FakeConnection())
    app.dependency_overrides[admin_routes.get_current_admin] = lambda: {
        "admin_id": 1,
        "admin_email": "admin@example.com",
    }
    monkeypatch.setattr(admin_routes, "_worker_request", fake_worker_request)
    try:
        client = TestClient(app)
        response = client.post("/api/admin/schedules/1/dry-run")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json()["decision"]["reason"] == "not-due"
    assert calls[0][0:2] == ("POST", "/internal/schedules/dry-run")
    schedule = calls[0][2]["schedule"]
    assert schedule["name"] == "report-collection"
    assert schedule["report_limit"] == 50
    assert schedule["backpressure_max_waiting"] == 20
