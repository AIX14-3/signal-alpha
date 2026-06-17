import json
import unittest

from signal_alpha_data_access.repositories.dead_letter import DeadLetterRepository


class FakeConnection:
    def __init__(self, *, fetch_rows=None, execute_result="INSERT 0 3"):
        self.calls = []
        self.fetch_rows = fetch_rows or []
        self.execute_result = execute_result

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.fetch_rows

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 7}

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return self.execute_result


class DeadLetterRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_archive_failed_task_is_idempotent_on_processing_queue_id(self):
        connection = FakeConnection()
        repository = DeadLetterRepository(connection)

        await repository.archive_failed_task(
            processing_queue_id=50,
            stock_id=1,
            task_type="normalize_dart",
            priority="batch",
            source_raw_ids=[20],
            source_signal_event_ids=None,
            source_analysis_result_ids=None,
            task_context={"stock_code": "005930"},
            final_error_message="boom",
            final_retry_count=3,
        )

        sql = connection.calls[0][1]
        args = connection.calls[0][2]
        self.assertIn("INSERT INTO dead_letter", sql)
        self.assertIn("ON CONFLICT (processing_queue_id) DO NOTHING", sql)
        self.assertEqual(args[0], 50)
        self.assertEqual(args[1], 1)
        # task_context serialized to JSON text for the ::JSONB bind.
        self.assertEqual(json.loads(args[7])["stock_code"], "005930")
        self.assertEqual(args[9], 3)

    async def test_archive_allows_null_stock_id_for_datalab(self):
        connection = FakeConnection()
        repository = DeadLetterRepository(connection)

        await repository.archive_failed_task(
            processing_queue_id=51,
            stock_id=None,
            task_type="normalize_datalab",
            priority="batch",
            source_raw_ids=None,
            source_signal_event_ids=None,
            source_analysis_result_ids=None,
            task_context=None,
            final_error_message=None,
            final_retry_count=1,
        )

        args = connection.calls[0][2]
        self.assertIsNone(args[1])
        self.assertIsNone(args[7])  # _jsonb(None) -> None

    async def test_reconcile_failed_archives_unarchived_failed_rows(self):
        connection = FakeConnection(execute_result="INSERT 0 4")
        repository = DeadLetterRepository(connection)

        archived = await repository.reconcile_failed(limit=100)

        self.assertEqual(archived, 4)
        sql = connection.calls[0][1]
        self.assertIn("FROM processing_queue pq", sql)
        self.assertIn("pq.status = 'failed'", sql)
        self.assertIn("NOT EXISTS", sql)
        self.assertIn("ON CONFLICT (processing_queue_id) DO NOTHING", sql)
        self.assertEqual(connection.calls[0][2], (100,))

    async def test_list_dead_letters_filters_task_type_and_replayed(self):
        connection = FakeConnection(fetch_rows=[{"id": 1}])
        repository = DeadLetterRepository(connection)

        await repository.list_dead_letters(task_type="normalize_dart", replayed=False, limit=25)

        sql, args = connection.calls[0][1], connection.calls[0][2]
        self.assertIn("FROM dead_letter", sql)
        self.assertIn("replayed_at IS NULL", sql)
        self.assertEqual(args, ("normalize_dart", False, 25))

    async def test_list_by_ids_returns_empty_without_query_when_no_ids(self):
        connection = FakeConnection()
        repository = DeadLetterRepository(connection)

        rows = await repository.list_by_ids([])

        self.assertEqual(rows, [])
        self.assertEqual(connection.calls, [])

    async def test_mark_replayed_sets_replayed_columns(self):
        connection = FakeConnection()
        repository = DeadLetterRepository(connection)

        await repository.mark_replayed(dead_letter_id=7, replayed_task_id=99)

        sql, args = connection.calls[0][1], connection.calls[0][2]
        self.assertIn("replayed_at = NOW()", sql)
        self.assertIn("replayed_task_id = $2", sql)
        self.assertEqual(args, (7, 99))

    async def test_dead_letter_stats_groups_by_task_type(self):
        connection = FakeConnection(fetch_rows=[{"task_type": "x", "total": 2, "unreplayed": 1}])
        repository = DeadLetterRepository(connection)

        await repository.dead_letter_stats()

        sql = connection.calls[0][1]
        self.assertIn("FROM dead_letter", sql)
        self.assertIn("GROUP BY task_type", sql)
        self.assertIn("FILTER (WHERE replayed_at IS NULL)", sql)


if __name__ == "__main__":
    unittest.main()
