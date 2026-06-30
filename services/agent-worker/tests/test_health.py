import unittest
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning
)
from starlette.testclient import TestClient

from app.core.database import get_database_pool
from app.main import app


class _FakeConnection:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def fetchval(self, *args, **kwargs):
        if self._fail:
            raise RuntimeError("connection refused")
        return 1


class _FakeAcquire:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def __aenter__(self) -> _FakeConnection:
        return _FakeConnection(fail=self._fail)

    async def __aexit__(self, *args) -> bool:
        return False


class _FakePool:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(fail=self._fail)


class _FakeTask:
    def __init__(self, *, done: bool = False, cancelled: bool = False) -> None:
        self._done = done
        self._cancelled = cancelled

    def done(self) -> bool:
        return self._done

    def cancelled(self) -> bool:
        return self._cancelled


class _FakeQueueDrainStatus:
    def snapshot(self):
        return {
            "cycles_completed": 3,
            "last_started_at": "2026-06-30T10:00:00+00:00",
            "last_finished_at": "2026-06-30T10:00:01+00:00",
            "last_cycle": {"total_runs": 2, "stopped_reason": "plan_exhausted"},
            "last_error": None,
        }


class HealthCheckTest(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_database_pool, None)
        for attr in (
            "price_collector_task",
            "ops_daemon_task",
            "queue_drain_task",
            "queue_drain_status",
        ):
            if hasattr(app.state, attr):
                delattr(app.state, attr)

    def test_health_returns_ok_when_db_reachable(self) -> None:
        # /health 는 DB 연결을 검증한다 → 도달 가능한 풀을 주입하면 200.
        app.dependency_overrides[get_database_pool] = lambda: _FakePool()
        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "service": "agent-worker",
                "version": "0.1.0",
                "runtime": {
                    "publishing": {
                        "backend_database_configured": False,
                        "mode": "single_db_noop",
                        "status": "disabled",
                        "warning": "BACKEND_DATABASE_URL is not configured; PUBLISH_SIGNALS tasks are skipped.",
                    },
                    "price_collector": {"enabled": False, "state": "not_started"},
                    "hiring_ops_daemon": {"enabled": False, "state": "not_started"},
                    "queue_drain_daemon": {
                        "enabled": False,
                        "state": "not_started",
                        "cycles_completed": 0,
                        "last_started_at": None,
                        "last_finished_at": None,
                        "last_cycle": None,
                        "last_error": None,
                    },
                },
            }
        )

    def test_health_reports_daemon_runtime_state(self) -> None:
        app.dependency_overrides[get_database_pool] = lambda: _FakePool()
        app.state.price_collector_task = _FakeTask(done=False)
        app.state.ops_daemon_task = _FakeTask(done=True)
        app.state.queue_drain_task = _FakeTask(done=True, cancelled=True)
        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runtime"]["price_collector"]["state"], "running")
        self.assertEqual(response.json()["runtime"]["hiring_ops_daemon"]["state"], "stopped")
        self.assertEqual(response.json()["runtime"]["queue_drain_daemon"]["state"], "cancelled")

    def test_health_reports_queue_drain_cycle_status(self) -> None:
        app.dependency_overrides[get_database_pool] = lambda: _FakePool()
        app.state.queue_drain_task = _FakeTask(done=False)
        app.state.queue_drain_status = _FakeQueueDrainStatus()
        client = TestClient(app)

        response = client.get("/health")

        queue_runtime = response.json()["runtime"]["queue_drain_daemon"]
        self.assertEqual(queue_runtime["state"], "running")
        self.assertEqual(queue_runtime["cycles_completed"], 3)
        self.assertEqual(queue_runtime["last_cycle"]["total_runs"], 2)
        self.assertIsNone(queue_runtime["last_error"])

    def test_health_reports_publish_backend_configuration(self) -> None:
        app.dependency_overrides[get_database_pool] = lambda: _FakePool()
        client = TestClient(app)

        response = client.get("/health")

        publishing = response.json()["runtime"]["publishing"]
        self.assertEqual(publishing["status"], "disabled")
        self.assertEqual(publishing["mode"], "single_db_noop")
        self.assertFalse(publishing["backend_database_configured"])
        self.assertIn("BACKEND_DATABASE_URL", publishing["warning"])

    def test_health_returns_503_when_db_unavailable(self) -> None:
        # DB 연결/쿼리 실패 → 503 (Cloud Run/GCE 헬스체크가 장애를 감지).
        app.dependency_overrides[get_database_pool] = lambda: _FakePool(fail=True)
        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
