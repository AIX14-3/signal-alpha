import unittest
from datetime import datetime, timezone

from signal_alpha_data_access.repositories.observability import ObservabilityRepository


class FakeConnection:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.rows

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return 7


class ObservabilityRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_queue_stats_groups_by_task_type_and_status(self):
        connection = FakeConnection()
        repository = ObservabilityRepository(connection)

        await repository.queue_stats()

        sql = connection.calls[0][1]
        self.assertIn("FROM processing_queue", sql)
        self.assertIn("GROUP BY task_type, status", sql)
        self.assertIn("FILTER", sql)  # oldest_waiting_at filtered to pending/retrying

    async def test_recent_failed_count_windows_failed_by_updated_at(self):
        # failed 는 종착 상태라 총계는 평생 누적 — backpressure 용 카운트는 updated_at
        # 기준 최근 윈도우로 제한돼야 한다.
        connection = FakeConnection()
        repository = ObservabilityRepository(connection)

        count = await repository.recent_failed_count(window_minutes=360)

        self.assertEqual(count, 7)
        kind, sql, args = connection.calls[0]
        self.assertEqual(kind, "fetchval")
        self.assertIn("status = 'failed'", sql)
        self.assertIn("updated_at >= NOW() - make_interval(mins => $1)", sql)
        self.assertEqual(args, (360,))

    async def test_collector_run_stats_binds_since_and_type(self):
        connection = FakeConnection()
        repository = ObservabilityRepository(connection)
        since = datetime(2026, 6, 15, 15, 0, tzinfo=timezone.utc)

        await repository.collector_run_stats(since=since, collector_type="HIRING")

        sql, args = connection.calls[0][1], connection.calls[0][2]
        self.assertIn("FROM collector_runs", sql)
        self.assertIn("GROUP BY collector_type", sql)
        self.assertIn("started_at >= $1", sql)
        self.assertEqual(args, (since, "HIRING"))

    async def test_collector_run_stats_defaults_are_null(self):
        connection = FakeConnection()
        repository = ObservabilityRepository(connection)

        await repository.collector_run_stats()

        self.assertEqual(connection.calls[0][2], (None, None))

    async def test_recent_collector_runs_orders_and_limits(self):
        connection = FakeConnection()
        repository = ObservabilityRepository(connection)

        await repository.recent_collector_runs(limit=5, collector_type="PATENT")

        sql, args = connection.calls[0][1], connection.calls[0][2]
        self.assertIn("FROM collector_runs", sql)
        self.assertIn("ORDER BY started_at DESC", sql)
        self.assertEqual(args, (5, "PATENT"))


if __name__ == "__main__":
    unittest.main()
