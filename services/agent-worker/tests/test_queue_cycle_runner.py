import unittest

from app.orchestrator.queue.tasks import DEFAULT_CYCLE_PLAN, QueueCycleRunner


class FakeRunner:
    """run_next 를 흉내내는 페이크 — task_type 별 pending 수만큼 success 반환 후 idle."""

    def __init__(self, pending: dict[str, int]) -> None:
        self.pending = dict(pending)
        self.calls: list[str] = []

    async def run_next(self, task_type: str) -> dict[str, str]:
        self.calls.append(task_type)
        if self.pending.get(task_type, 0) > 0:
            self.pending[task_type] -= 1
            return {"status": "success", "task_type": task_type}
        return {"status": "idle", "task_type": task_type}


class QueueCycleRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_caps_prevent_monopolization(self):
        # collect_dart 100개 대기여도 캡 2를 넘지 않는다(독점 방지의 핵심).
        runner = FakeRunner({"collect_dart": 100, "analyze_price": 100})
        summary = await QueueCycleRunner(runner).run_cycle(
            {"collect_dart": 2, "analyze_price": 50}
        )
        self.assertEqual(summary["counts"]["collect_dart"], 2)
        self.assertEqual(summary["counts"]["analyze_price"], 50)

    async def test_round_robin_interleaves(self):
        # 한 패스당 type 당 1개씩 — 순차가 아니라 교차 처리된다.
        runner = FakeRunner({"a": 5, "b": 5})
        await QueueCycleRunner(runner).run_cycle({"a": 5, "b": 5})
        # 처리 호출이 a,b,a,b... 로 교차(=a 를 끝까지 비우고 b 로 넘어가지 않음).
        self.assertEqual(runner.calls[:4], ["a", "b", "a", "b"])

    async def test_idle_short_circuit(self):
        # 대기보다 캡이 크면 대기 수만큼만 처리하고 idle 로 빠진다.
        runner = FakeRunner({"a": 3})
        summary = await QueueCycleRunner(runner).run_cycle({"a": 10})
        self.assertEqual(summary["counts"]["a"], 3)

    async def test_failed_does_not_starve_others(self):
        # 한 type 이 계속 failed 여도 사이클을 멈추지 않고 다른 type 을 끝까지 처리한다.
        class FailRunner(FakeRunner):
            async def run_next(self, task_type):
                self.calls.append(task_type)
                if task_type == "a":
                    return {"status": "failed", "task_type": task_type}
                if self.pending.get(task_type, 0) > 0:
                    self.pending[task_type] -= 1
                    return {"status": "success", "task_type": task_type}
                return {"status": "idle", "task_type": task_type}

        runner = FailRunner({"b": 3})
        summary = await QueueCycleRunner(runner).run_cycle({"a": 3, "b": 3})
        self.assertEqual(summary["counts"]["b"], 3)
        self.assertEqual(summary["failures"]["a"], 3)

    async def test_default_plan_has_all_collectors(self):
        # 기본 plan 에 price/alternative/dart/report/ml/발행 type 이 모두 포함(과거 drain 리스트엔
        # price/alternative 가 빠져 있었다).
        for task_type in ("analyze_price", "ANALYZE_ALTERNATIVE", "collect_dart", "publish_signals"):
            self.assertIn(task_type, DEFAULT_CYCLE_PLAN)


if __name__ == "__main__":
    unittest.main()
