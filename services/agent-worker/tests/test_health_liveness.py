"""드레인 데몬 전진 감지 liveness(/health/live) 단위 테스트.

라이브락(데몬이 얼었는데 DB 는 살아 /health 는 200)을 잡아 k8s 재시작을 유도하는 게 핵심.
HTTP 스택 없이 순수 판정 함수(_drain_liveness)만 검증한다.
"""

import unittest
from datetime import datetime, timedelta, timezone

from app.api.routes.health import _age_seconds, _drain_liveness


class FakeSettings:
    def __init__(self, *, enabled=True, max_stale=30.0):
        self.queue_drain_daemon_enabled = enabled
        self.queue_drain_liveness_max_stale_sec = max_stale


class FakeTask:
    def __init__(self, *, cancelled=False, done=False):
        self._cancelled = cancelled
        self._done = done

    def cancelled(self):
        return self._cancelled

    def done(self):
        return self._done


class FakeStatus:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")


class DrainLivenessTest(unittest.TestCase):
    def test_disabled_daemon_is_always_alive(self):
        detail = _drain_liveness(FakeSettings(enabled=False), task=None, status=None)
        self.assertTrue(detail["alive"])

    def test_task_not_running_is_not_alive(self):
        detail = _drain_liveness(FakeSettings(), task=None, status=None)
        self.assertFalse(detail["alive"])
        self.assertEqual(detail["task_state"], "not_started")

    def test_stopped_task_is_not_alive(self):
        detail = _drain_liveness(FakeSettings(), task=FakeTask(done=True), status=FakeStatus({}))
        self.assertFalse(detail["alive"])

    def test_recent_cycle_is_alive(self):
        status = FakeStatus({"last_finished_at": _iso(2), "cycles_completed": 5})
        detail = _drain_liveness(FakeSettings(max_stale=30.0), task=FakeTask(), status=status)
        self.assertTrue(detail["alive"])
        self.assertEqual(detail["reason"], "progressing")

    def test_stale_cycle_is_not_alive(self):
        status = FakeStatus(
            {"last_finished_at": _iso(1000), "cycles_completed": 5, "last_error": None}
        )
        detail = _drain_liveness(FakeSettings(max_stale=30.0), task=FakeTask(), status=status)
        self.assertFalse(detail["alive"])
        self.assertEqual(detail["reason"], "drain_stalled")

    def test_starting_without_marker_is_alive(self):
        status = FakeStatus({"cycles_completed": 0})
        detail = _drain_liveness(FakeSettings(), task=FakeTask(), status=status)
        self.assertTrue(detail["alive"])
        self.assertEqual(detail["reason"], "starting")

    def test_falls_back_to_started_at_when_not_yet_finished(self):
        status = FakeStatus({"last_started_at": _iso(1000), "cycles_completed": 0})
        detail = _drain_liveness(FakeSettings(max_stale=30.0), task=FakeTask(), status=status)
        self.assertFalse(detail["alive"])  # 시작만 하고 첫 사이클을 못 마친 채 정체

    def test_age_seconds_handles_bad_input(self):
        self.assertIsNone(_age_seconds("not-a-timestamp"))
        self.assertGreaterEqual(_age_seconds(_iso(5)), 4.0)


if __name__ == "__main__":
    unittest.main()
