import unittest
import json

from signal_alpha_data_access.repositories.processing_queue import ProcessingQueueRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if hasattr(self, "fetch_results"):
            return self.fetch_results.pop(0)
        return [{"id": 50, "task_type": "normalize_dart", "status": "pending"}]

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if hasattr(self, "fetchval_results"):
            return self.fetchval_results.pop(0)
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

    async def test_enqueue_with_status_reports_reused_existing_task(self):
        connection = FakeDuplicateConnection()
        repository = ProcessingQueueRepository(connection)

        result = await repository.enqueue_with_status(
            stock_id=1,
            task_type="normalize_report",
            source_raw_ids=[20],
            priority="batch",
            task_context={"raw_document_id": 20, "source_type": "REPORT"},
            dedupe=True,
        )

        self.assertEqual(result, {"task_id": 49, "reused": True})
        self.assertIn("status IN ('pending', 'running', 'retrying')", connection.calls[0][1])
        self.assertEqual(len(connection.calls), 1)

    async def test_enqueue_with_status_reports_inserted_task(self):
        connection = FakeConnection()
        repository = ProcessingQueueRepository(connection)

        result = await repository.enqueue_with_status(
            stock_id=1,
            task_type="normalize_report",
            source_raw_ids=[20],
            priority="batch",
            task_context={"raw_document_id": 20, "source_type": "REPORT"},
            dedupe=False,
        )

        self.assertEqual(result, {"task_id": 50, "reused": False})
        self.assertIn("INSERT INTO processing_queue", connection.calls[0][1])

    async def test_enqueue_dedupe_compares_source_id_arrays(self):
        connection = FakeDuplicateConnection()
        repository = ProcessingQueueRepository(connection)

        task_id = await repository.enqueue(
            stock_id=1,
            task_type="normalize_dart",
            priority="batch",
            source_raw_ids=[20],
            source_signal_event_ids=[30],
            source_analysis_result_ids=[40],
            task_context={"stock_code": "005930", "source_type": "DART"},
            dedupe=True,
        )

        self.assertEqual(task_id, 49)
        self.assertIn("source_raw_ids IS NOT DISTINCT FROM", connection.calls[0][1])
        self.assertIn("source_signal_event_ids IS NOT DISTINCT FROM", connection.calls[0][1])
        self.assertIn("source_analysis_result_ids IS NOT DISTINCT FROM", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2][3:6], ([20], [30], [40]))

    async def test_claim_next_pending_uses_skip_locked(self):
        connection = FakeConnection()
        repository = ProcessingQueueRepository(connection)

        row = await repository.claim_next_pending(task_type="normalize_report")

        self.assertEqual(row["status"], "running")
        self.assertIn("FOR UPDATE SKIP LOCKED", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], ("normalize_report",))

    async def test_claim_pending_by_id_restricts_to_requested_task(self):
        connection = FakeConnection()
        repository = ProcessingQueueRepository(connection)

        row = await repository.claim_pending_by_id(task_id=77, task_type="collect_dart")

        self.assertEqual(row["status"], "running")
        self.assertIn("WHERE id = $1", connection.calls[0][1])
        self.assertIn("task_type = $2", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], (77, "collect_dart"))

    async def test_mark_success_clears_previous_error_message(self):
        connection = FakeConnection()
        repository = ProcessingQueueRepository(connection)

        await repository.mark_success(task_id=50)

        self.assertIn("error_message = NULL", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], (50,))

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

    async def test_list_tasks_filters_by_stock_task_type_and_status(self):
        connection = FakeConnection()
        repository = ProcessingQueueRepository(connection)

        rows = await repository.list_tasks(
            stock_code="005930",
            task_type="normalize_dart",
            status="pending",
            limit=25,
        )

        self.assertEqual(rows[0]["id"], 50)
        self.assertIn("INNER JOIN stocks", connection.calls[0][1])
        self.assertIn("stocks.ticker = $1", connection.calls[0][1])
        self.assertIn("processing_queue.task_type = $2", connection.calls[0][1])
        self.assertIn("processing_queue.status = $3", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], ("005930", "normalize_dart", "pending", 25))

    async def test_retry_task_resets_failed_task_to_retrying(self):
        connection = FakeConnection()
        repository = ProcessingQueueRepository(connection)

        row = await repository.retry_task(task_id=77)

        self.assertEqual(row["status"], "running")
        self.assertIn("status = 'retrying'", connection.calls[0][1])
        self.assertIn("error_message = NULL", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], (77,))

    async def test_count_tasks_by_status_groups_by_requested_statuses(self):
        connection = FakeConnection()
        connection.fetch_results = [
            [
                {"status": "pending", "task_count": 2},
                {"status": "retrying", "task_count": 1},
            ]
        ]
        repository = ProcessingQueueRepository(connection)

        result = await repository.count_tasks_by_status(
            task_type="backfill_dart_labels",
            statuses=("pending", "retrying", "running"),
        )

        self.assertEqual(result, {"pending": 2, "retrying": 1})
        sql = connection.calls[0][1]
        self.assertIn("COUNT(*)::INT AS task_count", sql)
        self.assertIn("GROUP BY status", sql)
        self.assertEqual(
            connection.calls[0][2],
            ("backfill_dart_labels", ["pending", "retrying", "running"]),
        )

    async def test_mark_tasks_skipped_by_type_updates_only_limited_active_rows(self):
        connection = FakeConnection()
        connection.fetchval_results = [3]
        repository = ProcessingQueueRepository(connection)

        updated_count = await repository.mark_tasks_skipped_by_type(
            task_type="backfill_dart_labels",
            statuses=("pending", "retrying", "running"),
            message="legacy task removed",
            limit=100,
        )

        self.assertEqual(updated_count, 3)
        sql = connection.calls[0][1]
        self.assertIn("WITH target_tasks AS", sql)
        self.assertIn("status = 'skipped'", sql)
        self.assertIn("finished_at = NOW()", sql)
        self.assertNotIn("DELETE FROM processing_queue", sql)
        self.assertEqual(
            connection.calls[0][2],
            (
                "backfill_dart_labels",
                ["pending", "retrying", "running"],
                "legacy task removed",
                100,
            ),
        )

    async def test_mark_tasks_skipped_by_type_rejects_non_positive_limit(self):
        connection = FakeConnection()
        repository = ProcessingQueueRepository(connection)

        with self.assertRaises(ValueError):
            await repository.mark_tasks_skipped_by_type(
                task_type="backfill_dart_labels",
                statuses=("pending",),
                message="legacy task removed",
                limit=0,
            )

        self.assertEqual(connection.calls, [])


class _ProbeConnection(FakeConnection):
    def __init__(self, result):
        super().__init__()
        self._result = result

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self._result


class HasOpenOrSuccessfulTaskTest(unittest.IsolatedAsyncioTestCase):
    async def test_true_when_live_or_successful_task_exists(self):
        connection = _ProbeConnection(result=1)
        repository = ProcessingQueueRepository(connection)

        found = await repository.has_open_or_successful_task(
            raw_document_id=42, task_type="NORMALIZE_PATENT"
        )

        self.assertTrue(found)
        sql = connection.calls[0][1]
        self.assertIn("status IN ('pending', 'running', 'retrying', 'success')", sql)
        self.assertIn("$2 = ANY(source_raw_ids)", sql)
        self.assertEqual(connection.calls[0][2], ("NORMALIZE_PATENT", 42))

    async def test_false_when_only_failed_or_no_task(self):
        connection = _ProbeConnection(result=None)
        repository = ProcessingQueueRepository(connection)

        found = await repository.has_open_or_successful_task(
            raw_document_id=42, task_type="NORMALIZE_DATALAB"
        )

        self.assertFalse(found)
