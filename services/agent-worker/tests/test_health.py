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


class HealthCheckTest(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_database_pool, None)
        for attr in ("price_collector_task", "ops_daemon_task", "queue_drain_task"):
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
                    "price_collector": {"enabled": False, "state": "not_started"},
                    "hiring_ops_daemon": {"enabled": False, "state": "not_started"},
                    "queue_drain_daemon": {"enabled": False, "state": "not_started"},
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

    def test_health_returns_503_when_db_unavailable(self) -> None:
        # DB 연결/쿼리 실패 → 503 (Cloud Run/GCE 헬스체크가 장애를 감지).
        app.dependency_overrides[get_database_pool] = lambda: _FakePool(fail=True)
        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
