"""파이프라인 큐 정지/적체 알림 판정(_queue_stall_reason) 단위 테스트.

hiring 수집 한정 알림을 파이프라인 전역(processing_queue 백로그)으로 넓힌 부분. 백로그가 임계
초과 + 미감소일 때만(대량 배치 직후 일시 적체는 정상) 알린다.
"""

import unittest

from app.observability.ops_daemon import _queue_stall_reason


class FakeSettings:
    def __init__(
        self,
        *,
        backlog_threshold=500,
        failed_threshold=200,
        failed_window=360,
    ):
        self.ops_queue_backlog_alert_threshold = backlog_threshold
        self.ops_queue_failed_recent_alert_threshold = failed_threshold
        self.ops_queue_failed_window_minutes = failed_window


class QueueStallReasonTest(unittest.TestCase):
    def test_backlog_below_threshold_is_ok(self):
        reason = _queue_stall_reason(
            backlog=100, failed_recent=0, prev_backlog=100, settings=FakeSettings()
        )
        self.assertIsNone(reason)

    def test_high_but_draining_backlog_is_ok(self):
        # 백로그가 임계 초과지만 직전보다 줄고 있으면(드레인 중) 알리지 않는다.
        reason = _queue_stall_reason(
            backlog=600, failed_recent=0, prev_backlog=900, settings=FakeSettings()
        )
        self.assertIsNone(reason)

    def test_high_and_not_draining_backlog_alerts(self):
        reason = _queue_stall_reason(
            backlog=700, failed_recent=0, prev_backlog=650, settings=FakeSettings()
        )
        self.assertIsNotNone(reason)
        self.assertIn("드레인 정지", reason)

    def test_failed_spike_alerts(self):
        reason = _queue_stall_reason(
            backlog=0, failed_recent=250, prev_backlog=0, settings=FakeSettings()
        )
        self.assertIsNotNone(reason)
        self.assertIn("실패", reason)

    def test_thresholds_zero_disables(self):
        reason = _queue_stall_reason(
            backlog=99999,
            failed_recent=99999,
            prev_backlog=0,
            settings=FakeSettings(backlog_threshold=0, failed_threshold=0),
        )
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
