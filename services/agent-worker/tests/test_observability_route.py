import sys
import unittest
import warnings
from datetime import datetime
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

QUEUE_ROWS = [
    {"task_type": "ANALYZE_DATALAB", "status": "pending", "count": 3, "oldest_waiting_at": None},
    {"task_type": "ANALYZE_DATALAB", "status": "success", "count": 7, "oldest_waiting_at": None},
    {"task_type": "normalize_dart", "status": "failed", "count": 2, "oldest_waiting_at": None},
]

COLLECTOR_ROWS = [
    {
        "collector_type": "HIRING",
        "runs": 2,
        "runs_success": 1,
        "runs_partial": 1,
        "runs_failed": 0,
        "runs_running": 0,
        "collected": 100,
        "inserted": 70,
        "skipped": 15,
        "failed": 15,
        "last_started_at": datetime(2026, 6, 16, 1, 0),
    }
]

RECENT_ROWS = [
    {
        "id": 9,
        "collector_type": "HIRING",
        "run_mode": "manual",
        "status": "partial",
        "started_at": datetime(2026, 6, 16, 1, 0),
        "finished_at": datetime(2026, 6, 16, 1, 5),
        "collected_count": 100,
        "inserted_count": 70,
        "skipped_count": 15,
        "failed_count": 15,
        "error_message": None,
    }
]


FETCHVAL_CALLS: list[tuple[str, tuple]] = []


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if "GROUP BY task_type, status" in sql:
            return QUEUE_ROWS
        if "GROUP BY collector_type" in sql:
            return COLLECTOR_ROWS
        if "ORDER BY started_at DESC" in sql:
            return RECENT_ROWS
        return []

    async def fetchval(self, sql, *args):
        # recent_failed_count (failed backpressure 윈도우 카운트)
        FETCHVAL_CALLS.append((sql, args))
        return 1


class FakeAcquire:
    async def __aenter__(self):
        return FakeConnection()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


class ObservabilityRouteTest(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        self.client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_queue_stats_rolls_up_totals(self):
        resp = self.client.get("/internal/stats/queue")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 12)
        self.assertEqual(body["totals_by_status"]["pending"], 3)
        self.assertEqual(body["totals_by_status"]["failed"], 2)
        self.assertEqual(len(body["items"]), 3)
        # failed 총계(평생 누적)와 별도로 최근 윈도우 실패 수를 노출(스케줄러 backpressure 용).
        self.assertEqual(body["failed_recent"], 1)
        self.assertEqual(body["failed_window_minutes"], 360)

    def test_queue_stats_accepts_failed_window_minutes_param(self):
        FETCHVAL_CALLS.clear()
        resp = self.client.get("/internal/stats/queue?failed_window_minutes=120")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["failed_window_minutes"], 120)
        sql, args = FETCHVAL_CALLS[0]
        self.assertIn("status = 'failed'", sql)
        self.assertEqual(args, (120,))

    def test_collector_stats_computes_rates_and_utc_boundary(self):
        resp = self.client.get("/internal/stats/collectors?since=2026-06-16&collector_type=HIRING")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # KST 2026-06-16 00:00 -> UTC 2026-06-15T15:00
        self.assertTrue(body["since_utc"].startswith("2026-06-15T15:00"))
        item = body["items"][0]
        self.assertEqual(item["collector_type"], "HIRING")
        self.assertEqual(item["ingest_success_rate"], 85.0)
        self.assertEqual(item["failure_rate"], 15.0)
        self.assertEqual(item["runs"], 2)

    def test_recent_runs_returns_rows(self):
        resp = self.client.get("/internal/stats/collectors/runs?limit=5")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["collector_type"], "HIRING")


if __name__ == "__main__":
    unittest.main()
