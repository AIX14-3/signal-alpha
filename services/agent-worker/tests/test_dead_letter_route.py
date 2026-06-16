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

from app.core.database import get_database_pool
from app.main import app

DEAD_LETTER_ROW = {
    "id": 1,
    "processing_queue_id": 50,
    "stock_id": 1,
    "task_type": "normalize_dart",
    "priority": "batch",
    "source_raw_ids": [20],
    "source_signal_event_ids": None,
    "source_analysis_result_ids": None,
    "task_context": {"stock_code": "005930"},
    "final_error_message": "boom",
    "final_retry_count": 3,
    "replayed_at": None,
}


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "id = ANY($1::BIGINT[])" in sql:
            return [DEAD_LETTER_ROW]
        return [DEAD_LETTER_ROW]

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return 99  # new enqueued task id

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": args[0], "replayed_task_id": 99}

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "INSERT 0 2"


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, connection=None):
        self.connection = connection or FakeConnection()

    def acquire(self):
        return FakeAcquire(self.connection)


class DeadLetterRouteTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_list_dead_letters_returns_items(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app)

        response = client.get("/internal/queue/dead-letter", params={"replayed": False})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["task_type"], "normalize_dart")

    def test_replay_reenqueues_and_marks_replayed(self):
        connection = FakeConnection()
        app.dependency_overrides[get_database_pool] = lambda: FakePool(connection)
        client = TestClient(app)

        response = client.post(
            "/internal/queue/dead-letter/replay",
            json={"dead_letter_ids": [1]},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["replayed_count"], 1)
        self.assertEqual(body["results"][0]["replayed_task_id"], 99)
        # enqueue (fetchval) + mark_replayed (fetchrow) both happened.
        self.assertTrue(any(c[0] == "fetchval" and "INSERT INTO processing_queue" in c[1] for c in connection.calls))
        self.assertTrue(any(c[0] == "fetchrow" and "replayed_at = NOW()" in c[1] for c in connection.calls))

    def test_reconcile_returns_archived_count(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app)

        response = client.post("/internal/queue/dead-letter/reconcile", json={"limit": 100})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"archived_count": 2})


if __name__ == "__main__":
    unittest.main()
