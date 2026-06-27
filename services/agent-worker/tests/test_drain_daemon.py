import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.queue import drain_daemon
from app.orchestrator.queue.drain_daemon import DRAIN_ORDER, _resolve_order, drain_until_idle
from app.orchestrator.queue.task_types import AGGREGATE_SIGNAL, ANALYZE_DART, PUBLISH_SIGNALS


class _FakeConn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def acquire(self):
        return _FakeConn()


class _FakeRunner:
    """run_task 호출 순서를 기록하고, 스크립트(pending)에 남은 task_type만 success 반환."""

    calls: list[str] = []

    def __init__(self, conn, handlers, *, pending):
        self._pending = pending

    async def run_task(self, task_type):
        _FakeRunner.calls.append(task_type)
        if self._pending.get(task_type, 0) > 0:
            self._pending[task_type] -= 1
            return {"status": "success", "task_id": 1, "task_type": task_type, "result": {}}
        return {"status": "idle", "task_type": task_type}


class DrainOrderTest(unittest.TestCase):
    def test_publish_signals_is_in_drain_order(self):
        # 발행이 드레인 순회에 포함돼야 워커가 리포트를 끝단까지 발행한다(#11).
        self.assertIn(PUBLISH_SIGNALS, DRAIN_ORDER)

    def test_drain_order_has_no_duplicates(self):
        self.assertEqual(len(DRAIN_ORDER), len(set(DRAIN_ORDER)))

    def test_publish_runs_after_aggregate(self):
        self.assertLess(DRAIN_ORDER.index(AGGREGATE_SIGNAL), DRAIN_ORDER.index(PUBLISH_SIGNALS))

    def test_resolve_order_appends_unknown_handler_keys(self):
        order = _resolve_order([ANALYZE_DART, "brand_new_task"])
        self.assertEqual(order[0], ANALYZE_DART)
        self.assertIn("brand_new_task", order)


class DrainUntilIdleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _FakeRunner.calls = []

    async def test_drains_until_all_idle_in_chain_order(self):
        pending = {ANALYZE_DART: 1, AGGREGATE_SIGNAL: 1, PUBLISH_SIGNALS: 1}
        handlers = {tt: object() for tt in DRAIN_ORDER}

        def fake_runner(conn, h):
            return _FakeRunner(conn, h, pending=pending)

        original = drain_daemon.QueueTaskRunner
        drain_daemon.QueueTaskRunner = fake_runner
        try:
            counts = await drain_until_idle(
                _FakePool(), handler_factory=lambda conn: handlers
            )
        finally:
            drain_daemon.QueueTaskRunner = original

        # 3건 모두 소비되고, 전 패스 idle 로 종료.
        self.assertEqual(counts, {"success": 3})
        self.assertEqual(pending, {ANALYZE_DART: 0, AGGREGATE_SIGNAL: 0, PUBLISH_SIGNALS: 0})
        # 첫 패스에서 ANALYZE_DART → AGGREGATE_SIGNAL → PUBLISH_SIGNALS 순으로 호출됐다.
        first_pass = _FakeRunner.calls[: len(DRAIN_ORDER)]
        self.assertLess(first_pass.index(ANALYZE_DART), first_pass.index(AGGREGATE_SIGNAL))
        self.assertLess(first_pass.index(AGGREGATE_SIGNAL), first_pass.index(PUBLISH_SIGNALS))


if __name__ == "__main__":
    unittest.main()
