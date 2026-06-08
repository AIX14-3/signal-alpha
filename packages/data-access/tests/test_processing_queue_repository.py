import unittest

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
        return "OK"


class ProcessingQueueRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_returns_new_task_id(self):
        connection = FakeConnection()
        repository = ProcessingQueueRepository(connection)

        task_id = await repository.enqueue(
            stock_id=1,
            task_type="normalize_report",
            source_raw_ids=[20],
            priority="batch",
        )

        self.assertEqual(task_id, 50)
        self.assertEqual(connection.calls[0][2][0:3], (1, "normalize_report", "batch"))

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
        self.assertEqual(connection.calls[0][2], (50, "timeout", "retrying"))
