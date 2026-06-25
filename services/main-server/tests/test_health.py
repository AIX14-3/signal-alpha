import unittest
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning
)
from starlette.testclient import TestClient

from app.api.routes.health import get_database_pool
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


class HealthCheckTest(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_database_pool, None)

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
                "service": "main-server",
                "version": "0.1.0"
            }
        )

    def test_health_returns_503_when_db_unavailable(self) -> None:
        # DB 연결/쿼리 실패 → 503 (Cloud Run 헬스체크가 장애를 감지).
        app.dependency_overrides[get_database_pool] = lambda: _FakePool(fail=True)
        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
