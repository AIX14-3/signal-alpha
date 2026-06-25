"""DB-aware /health: 풀에서 SELECT 1 로 실제 연결을 확인하고 끊김 시 503."""

import unittest
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.main import app


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Connection:
    def __init__(self, *, fail=False):
        self.fail = fail

    async def execute(self, sql, *args):
        if self.fail:
            raise RuntimeError("connection lost")
        return "SELECT 1"


class _Pool:
    def __init__(self, *, fail=False):
        self._connection = _Connection(fail=fail)

    def acquire(self):
        return _Acquire(self._connection)


class HealthRouteTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        app.state.database_pool = None

    def test_health_ok_when_db_reachable(self):
        app.state.database_pool = _Pool()
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["db"], "ok")

    def test_health_503_when_pool_uninitialized(self):
        app.state.database_pool = None
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["db"], "uninitialized")

    def test_health_503_when_db_query_fails(self):
        app.state.database_pool = _Pool(fail=True)
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["db"], "error")


if __name__ == "__main__":
    unittest.main()
