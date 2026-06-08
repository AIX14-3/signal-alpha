import unittest
import json

from signal_alpha_data_access.repositories.processing_queue import ProcessingQueueRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return 50

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 50, "status": "running"}

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        if hasattr(self, "execute_results"):
            return self.execute_results.pop(0)
        return "OK"


class FakeDuplicateConnection(FakeConnection):
    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "SELECT id" in sql:
            return 49
        return 50


class ProcessingQueueRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_returns_new_task_id(self):
        connection = FakeConnection()
        repository = ProcessingQueueRepository(connection)

        task_id = await repository.enqueue(
            stock_id=1,
            task_type="normalize_report",
            source_raw_ids=[20],
            priority="batch",
            task_context={"stock_code": "005930"},
        )

        self.assertEqual(task_id, 50)
        self.assertEqual(connection.calls[0][2][0:3], (1, "normalize_report", "batch"))
        self.assertEqual(json.loads(connection.calls[0][2][6])["stock_code"], "005930")

    async def test_enqueue_returns_existing_active_task_when_dedupe_enabled(self):
        connection = FakeDuplicateConnection()
        repository = ProcessingQueueRepository(connection)

        task_id = await repository.enqueue(
            stock_id=1,
            task_type="collect_dart",
            priority="batch",
            task_context={"stock_code": "005930", "bgn_de": "20260601", "end_de": "20260608"},
            dedupe=True,
        )

        self.assertEqual(task_id, 49)
        self.assertIn("status IN ('pending', 'running', 'retrying')", connection.calls[0][1])
        self.assertEqual(len(connection.calls), 1)

    async def test_claim_next_pending_uses_skip_locked(self):
        connection = FakeConnection()
        repository = ProcessingQueueRepository(connection)

        row = await repository.claim_next_pending(task_type="normalize_report")

        self.assertEqual(row["status"], "running")
        self.assertIn("FOR UPDATE SKIP LOCKED", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], ("normalize_report",))

    async def test_mark_failed_can_schedule_retrying_status(self):
        connection = FakeConnection()
        repository = ProcessingQueueRepository(connection)

        await repository.mark_failed(task_id=50, error_message="timeout", retry=True)

        self.assertIn("retry_count = retry_count + 1", connection.calls[0][1])
        self.assertIn("$3::VARCHAR", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], (50, "timeout", "retrying"))

    async def test_sweep_stale_active_tasks_retries_and_fails_old_tasks(self):
        connection = FakeConnection()
        connection.execute_results = ["UPDATE 2", "UPDATE 1"]
        repository = ProcessingQueueRepository(connection)

        result = await repository.sweep_stale_active_tasks(
            running_timeout_minutes=30,
            retrying_timeout_minutes=120,
        )

        self.assertEqual(result, {"retried_count": 2, "failed_count": 1})
        self.assertIn("status = 'retrying'", connection.calls[0][1])
        self.assertIn("status = 'failed'", connection.calls[1][1])
        self.assertEqual(connection.calls[0][2], (30, "Task exceeded running timeout."))
        self.assertEqual(connection.calls[1][2], (30, 120, "Task exceeded retry timeout."))
