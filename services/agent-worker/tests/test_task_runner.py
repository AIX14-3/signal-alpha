import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.queue.tasks import QueueTaskRunner


class FakeConnection:
    def __init__(self, task):
        self.task = task
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.task

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "OK"


class QueueTaskRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_next_marks_success_after_handler(self):
        connection = FakeConnection({"id": 10, "task_type": "normalize_report", "retry_count": 0, "max_retry_count": 3})
        handled = []

        async def handler(task):
            handled.append(task["id"])
            return {"normalized": True}

        runner = QueueTaskRunner(connection, {"normalize_report": handler})

        result = await runner.run_next("normalize_report")

        self.assertEqual(result["status"], "success")
        self.assertEqual(handled, [10])
        self.assertTrue(any(call[0] == "execute" and "status = 'success'" in call[1] for call in connection.calls))

    async def test_run_next_returns_idle_when_no_task_exists(self):
        connection = FakeConnection(None)

        runner = QueueTaskRunner(connection, {})

        result = await runner.run_next("normalize_report")

        self.assertEqual(result, {"status": "idle", "task_type": "normalize_report"})

    async def test_run_next_marks_failed_with_retry_when_handler_raises(self):
        connection = FakeConnection({"id": 10, "task_type": "normalize_report", "retry_count": 0, "max_retry_count": 3})

        async def handler(task):
            raise RuntimeError("LLM timeout")

        runner = QueueTaskRunner(connection, {"normalize_report": handler})

        result = await runner.run_next("normalize_report")

        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["retry"])
        self.assertTrue(any(call[0] == "execute" and "retrying" in call[2] for call in connection.calls))
        # 재시도 여지가 있으면 dead_letter 아카이브하지 않는다.
        self.assertFalse(any("INSERT INTO dead_letter" in call[1] for call in connection.calls))

    async def test_run_next_archives_to_dead_letter_on_terminal_failure(self):
        # retry_count >= max_retry_count → 종착(retry=False) → dead_letter 아카이브.
        connection = FakeConnection({
            "id": 10,
            "task_type": "normalize_report",
            "priority": "batch",
            "stock_id": 1,
            "source_raw_ids": [20],
            "source_signal_event_ids": None,
            "source_analysis_result_ids": None,
            "task_context": {"stock_code": "005930"},
            "retry_count": 3,
            "max_retry_count": 3,
        })

        async def handler(task):
            raise RuntimeError("LLM timeout")

        runner = QueueTaskRunner(connection, {"normalize_report": handler})

        result = await runner.run_next("normalize_report")

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["retry"])
        archive_calls = [c for c in connection.calls if "INSERT INTO dead_letter" in c[1]]
        self.assertEqual(len(archive_calls), 1)
        # final_retry_count = pre-increment retry_count(3) + 1 = 4.
        self.assertEqual(archive_calls[0][2][0], 10)  # processing_queue_id
        self.assertEqual(archive_calls[0][2][9], 4)   # final_retry_count
