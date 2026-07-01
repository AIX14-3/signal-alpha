"""run_normalizers._drain_source 병렬 드레인 — N 워커가 큐를 끝까지 비우고 집계."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import run_normalizers


class _FakeAcquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def acquire(self):
        return _FakeAcquire()


class FakeRunner:
    """run_task 가 호출될 때마다 남은 작업이 있으면 success, 없으면 idle 반환.

    asyncio 단일 스레드 + run_task 내부에 await 없음 → 체크+감소가 원자적이라
    여러 워커가 동시에 호출해도 정확히 ``remaining`` 건만 success 로 처리된다.
    """

    remaining = 0
    transient_left = 0  # 이 횟수만큼 run_task 가 먼저 일시오류를 던진다(작업 미소모)

    def __init__(self, conn, handlers):
        pass

    async def run_task(self, task_type):
        if FakeRunner.transient_left > 0:
            FakeRunner.transient_left -= 1
            raise asyncio.TimeoutError("simulated transient DB error")
        if FakeRunner.remaining > 0:
            FakeRunner.remaining -= 1
            return {"status": "success", "task_id": FakeRunner.remaining}
        return {"status": "idle"}


async def _noop_sleep(*_a, **_k):
    return None


class TestDrainConcurrency(unittest.TestCase):
    def test_workers_drain_all_tasks_and_aggregate(self):
        FakeRunner.remaining = 25
        FakeRunner.transient_left = 0
        with patch.dict(run_normalizers._SOURCES,
                        {"PATENT": ("NORMALIZE_PATENT", lambda conn: object())}), \
                patch.object(run_normalizers, "QueueTaskRunner", FakeRunner):
            counts = asyncio.run(
                run_normalizers._drain_source(FakePool(), "PATENT", concurrency=4)
            )
        self.assertEqual(counts["success"], 25, "모든 작업이 정확히 한 번씩 처리돼야 한다")
        self.assertEqual(counts["error"], 0)
        self.assertEqual(FakeRunner.remaining, 0)

    def test_transient_errors_are_retried_not_fatal(self):
        # 일시 인프라 오류(statement timeout·연결끊김 등)가 나도 드레인이 죽지 않고
        # 백오프 후 재시도해 큐를 끝까지 비워야 한다.
        FakeRunner.remaining = 10
        FakeRunner.transient_left = 5
        with patch.dict(run_normalizers._SOURCES,
                        {"PATENT": ("NORMALIZE_PATENT", lambda conn: object())}), \
                patch.object(run_normalizers, "QueueTaskRunner", FakeRunner), \
                patch.object(run_normalizers.asyncio, "sleep", _noop_sleep):
            counts = asyncio.run(
                run_normalizers._drain_source(FakePool(), "PATENT", concurrency=2)
            )
        self.assertEqual(counts["success"], 10, "일시오류에도 모든 작업을 결국 처리해야 한다")
        self.assertEqual(counts["transient"], 5)
        self.assertEqual(FakeRunner.remaining, 0)


if __name__ == "__main__":
    unittest.main()
