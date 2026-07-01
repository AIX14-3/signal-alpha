import unittest
from datetime import time

from signal_alpha_data_access.repositories.collection_schedules import (
    CollectionScheduleRepository,
)


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 123, "status": "running"}

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [{"id": 123, "status": "ok"}]


class CollectionScheduleRunsTest(unittest.IsolatedAsyncioTestCase):
    async def test_update_config_updates_cadence_and_active_window_fields(self):
        connection = FakeConnection()
        repository = CollectionScheduleRepository(connection)

        await repository.update_config(
            schedule_id=1,
            enabled=True,
            run_at_local=time(8, 30),
            timezone="Asia/Seoul",
            targets=["dart"],
            dart_limit=100,
            price_modes=["snapshot"],
            frequency_minutes=60,
            active_from_local=time(8, 30),
            active_until_local=time(20, 30),
            updated_by="admin@example.com",
        )

        kind, sql, args = connection.calls[0]
        self.assertEqual(kind, "fetchrow")
        self.assertIn("frequency_minutes = COALESCE", sql)
        self.assertIn("active_from_local = COALESCE", sql)
        self.assertIn("active_until_local = COALESCE", sql)
        self.assertEqual(
            args,
            (
                1,
                True,
                time(8, 30),
                "Asia/Seoul",
                '["dart"]',
                100,
                '["snapshot"]',
                60,
                time(8, 30),
                time(20, 30),
                "admin@example.com",
            ),
        )

    async def test_start_run_inserts_schedule_run_with_json_targets(self):
        connection = FakeConnection()
        repository = CollectionScheduleRepository(connection)

        row = await repository.start_run(
            schedule_id=1,
            schedule_name="daily-collection",
            trigger_reason="manual",
            targets=["dart", "price"],
        )

        self.assertEqual(row["id"], 123)
        kind, sql, args = connection.calls[0]
        self.assertEqual(kind, "fetchrow")
        self.assertIn("INSERT INTO collection_schedule_runs", sql)
        self.assertEqual(args, (1, "daily-collection", "manual", '["dart", "price"]'))

    async def test_finish_run_updates_status_detail_and_finished_at(self):
        connection = FakeConnection()
        repository = CollectionScheduleRepository(connection)

        await repository.finish_run(
            run_id=123,
            status="ok",
            detail={"dart": 2},
        )

        kind, sql, args = connection.calls[0]
        self.assertEqual(kind, "fetchrow")
        self.assertIn("UPDATE collection_schedule_runs", sql)
        self.assertIn("finished_at = NOW()", sql)
        self.assertEqual(args, (123, "ok", '{"dart": 2}'))

    async def test_list_recent_runs_filters_by_schedule_and_limit(self):
        connection = FakeConnection()
        repository = CollectionScheduleRepository(connection)

        rows = await repository.list_recent_runs(schedule_id=1, limit=20)

        self.assertEqual(rows, [{"id": 123, "status": "ok"}])
        kind, sql, args = connection.calls[0]
        self.assertEqual(kind, "fetch")
        self.assertIn("FROM collection_schedule_runs", sql)
        self.assertIn("ORDER BY started_at DESC", sql)
        self.assertEqual(args, (1, 20))
