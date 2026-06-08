import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.tasks import QueueTaskRunner


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
