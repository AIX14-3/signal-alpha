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
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "FROM collection_schedules" in sql:
            return [
                {
                    "id": 1,
                    "name": "price-collection",
                    "enabled": True,
                    "run_at_local": time(9, 0),
                    "timezone": "Asia/Seoul",
                    "targets": ["price"],
                    "dart_limit": 10,
                    "price_modes": ["snapshot"],
                    "frequency_minutes": 60,
                    "active_from_local": time(9, 0),
                    "active_until_local": time(15, 30),
                    "last_run_at": datetime(2026, 7, 1, 8, 0, tzinfo=UTC),
                    "last_status": "success",
                    "last_detail": None,
                    "next_run_at": datetime(2026, 7, 1, 8, 30, tzinfo=UTC),
                    "manual_trigger_requested_at": None,
                    "updated_by": None,
                    "updated_at": None,
                },
                {
                    "id": 2,
                    "name": "report-collection",
                    "enabled": True,
                    "run_at_local": time(4, 30),
                    "timezone": "Asia/Seoul",
                    "targets": ["report"],
                    "dart_limit": 10,
                    "price_modes": [],
                    "frequency_minutes": 720,
                    "active_from_local": time(4, 30),
                    "active_until_local": time(20, 30),
                    "last_run_at": datetime(2026, 7, 1, 4, 30, tzinfo=UTC),
                    "last_status": "failed",
                    "last_detail": None,
                    "next_run_at": datetime(2099, 1, 1, 0, 0, tzinfo=UTC),
                    "manual_trigger_requested_at": None,
                    "updated_by": None,
                    "updated_at": None,
                },
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


class FakeSettings:
    agent_worker_internal_base_url = "http://worker"
    internal_api_token = "test-token"


async def fake_worker_request(method, path, *, settings, json_body=None, params=None):
    calls = fake_worker_request.calls
    calls.append((method, path, json_body, params, settings.internal_api_token))
    if path == "/internal/stats/queue":
        return {
            "total": 12,
            "totals_by_status": {"pending": 7, "retrying": 3, "failed": 2},
            "items": [{"task_type": "analyze_report", "status": "failed", "count": 2}],
            "dead_letter": {"total": 4, "unreplayed": 3, "items": []},
        }
    if path == "/internal/queue/tasks":
        return {
            "count": 1,
            "items": [{"id": 81, "task_type": "analyze_report", "status": "failed"}],
        }
    if path == "/internal/queue/dead-letter":
        return {
            "count": 1,
            "items": [{"id": 9, "task_type": "normalize_report", "replayed_at": None}],
        }
    if path == "/internal/queue/tasks/81/retry":
        return {"id": 81, "status": "pending"}
    if path == "/internal/queue/dead-letter/replay":
        return {"replayed_count": 1, "results": [{"dead_letter_id": 9, "replayed_task_id": 91}]}
    if path == "/internal/queue/sweep-stale":
        return {"running_retried": 1, "retrying_failed": 0}
    raise AssertionError(f"Unexpected worker path: {path}")


fake_worker_request.calls = []


def _install_overrides(monkeypatch, connection):
    fake_worker_request.calls = []
    monkeypatch.setattr(admin_routes, "_worker_request", fake_worker_request)
    app.dependency_overrides[admin_routes.get_settings] = lambda: FakeSettings()
    app.dependency_overrides[admin_routes.get_database_pool] = lambda: FakePool(connection)
    app.dependency_overrides[admin_routes.get_current_admin] = lambda: {
        "admin_id": 1,
        "admin_email": "admin@example.com",
    }


def test_admin_queue_overview_proxies_worker_state_and_builds_ops_events(monkeypatch):
    connection = FakeConnection()
    _install_overrides(monkeypatch, connection)
    try:
        client = TestClient(app)
        response = client.get("/api/admin/queue/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["queue"]["totals_by_status"]["failed"] == 2
    assert body["failed_tasks"]["items"][0]["id"] == 81
    assert body["dead_letters"]["items"][0]["id"] == 9
    assert body["schedule_summary"]["total"] == 2
    assert body["schedule_summary"]["by_health_status"]["failed_waiting"] == 1
    event_types = [event["type"] for event in body["events"]]
    assert "queue_backlog" in event_types
    assert "dead_letter_pending" in event_types
    assert "schedule_health" in event_types


def test_admin_queue_actions_proxy_retry_replay_and_sweep(monkeypatch):
    connection = FakeConnection()
    _install_overrides(monkeypatch, connection)
    try:
        client = TestClient(app)
        retry = client.post("/api/admin/queue/tasks/81/retry")
        replay = client.post("/api/admin/queue/dead-letter/replay", json={"dead_letter_ids": [9]})
        sweep = client.post("/api/admin/queue/sweep-stale", json={"running_timeout_minutes": 30})
    finally:
        app.dependency_overrides.clear()

    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "pending"
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed_count"] == 1
    assert sweep.status_code == 200, sweep.text
    assert sweep.json()["running_retried"] == 1
    assert ("POST", "/internal/queue/tasks/81/retry", None, None, "test-token") in fake_worker_request.calls
    assert (
        "POST",
        "/internal/queue/dead-letter/replay",
        {"dead_letter_ids": [9]},
        None,
        "test-token",
    ) in fake_worker_request.calls
