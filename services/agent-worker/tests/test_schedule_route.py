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

from app.api.routes.schedules import get_database_pool
from app.main import app


class FakeConnection:
    async def fetch(self, sql, *args):
        if "FROM processing_queue" in sql:
            return [
                {
                    "task_type": "collect_dart",
                    "status": "pending",
                    "count": 6,
                    "oldest_waiting_at": None,
                }
            ]
        return [
            {"id": 1, "ticker": "005930"},
            {"id": 2, "ticker": "000660"},
        ]

    async def fetchval(self, sql, *args):
        if "SELECT id" in sql:
            return None
        return 80 + args[0]


class FakeAcquire:
    async def __aenter__(self):
        return FakeConnection()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


class ScheduleRouteTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_schedule_dart_collection_enqueues_active_stocks(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

        response = client.post(
            "/internal/schedules/dart/collect",
            json={"limit": 2, "end_de": "20260610", "priority": "batch"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"scheduled_count": 2, "task_ids": [81, 82]})

    def test_schedule_dry_run_returns_scheduler_decision_without_enqueueing(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

        response = client.post(
            "/internal/schedules/dry-run",
            json={
                "schedule": {
                    "id": 1,
                    "name": "dart-collection",
                    "enabled": True,
                    "run_at_local": "08:30",
                    "timezone": "Asia/Seoul",
                    "targets": ["dart"],
                    "dart_limit": 100,
                    "price_modes": ["snapshot"],
                    "frequency_minutes": 60,
                    "active_from_local": "08:30",
                    "active_until_local": "20:30",
                    "last_run_at": "2026-07-01T08:30:00+09:00",
                    "manual_trigger_requested_at": None,
                    "backpressure_max_waiting": 5,
                },
                "now": "2026-07-01T09:31:00+09:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["would_fire"])
        self.assertEqual(body["decision"]["reason"], "queue-backlog")
        self.assertEqual(body["backpressure"]["waiting"], 6)

    def _dry_run_payload(self, **overrides):
        schedule = {
            "id": 1,
            "name": "dart-collection",
            "enabled": True,
            "run_at_local": "08:30",
            "timezone": "Asia/Seoul",
            "targets": ["dart"],
            "dart_limit": 100,
            "price_modes": ["snapshot"],
            "frequency_minutes": 60,
            "active_from_local": "08:30",
            "active_until_local": "20:30",
            "last_run_at": "2026-07-01T08:30:00+09:00",
            "manual_trigger_requested_at": None,
        }
        schedule.update(overrides.pop("schedule", {}))
        return {"schedule": schedule, **overrides}

    def test_schedule_dry_run_interprets_naive_now_in_schedule_timezone(self):
        # naive now(오프셋 없는 ISO)는 스케줄 타임존으로 해석 — 그대로 두면 aware
        # last_run_at 과의 비교에서 500 이 났다.
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

        response = client.post(
            "/internal/schedules/dry-run",
            json=self._dry_run_payload(now="2026-07-01T09:31:00"),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["evaluated_at"].endswith("+09:00"))
        self.assertEqual(body["evaluated_at"], "2026-07-01T09:31:00+09:00")

    def test_schedule_dry_run_rejects_unknown_timezone_with_422(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        client = TestClient(app, headers={"X-Internal-Token": "test-internal-token"})

        response = client.post(
            "/internal/schedules/dry-run",
            json=self._dry_run_payload(
                schedule={"timezone": "Mars/Olympus_Mons"},
                now="2026-07-01T09:31:00+09:00",
            ),
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("unknown timezone", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
