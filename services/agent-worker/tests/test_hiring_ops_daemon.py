"""Hiring 운영 알림/데몬(app/observability/ops_daemon.py) 단위 테스트 (Phase 5).

DB/네트워크 없이 pool·repository·send_discord_alert 를 mock 한다. 핵심 검증:
임계 판정(_alert_reason), self-healing 호출, run_id de-dup, cold-start warm-up,
cancel 전파.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from app.observability import ops_daemon
from app.observability.ops_daemon import (
    _alert_reason,
    _new_finished_runs,
    run_ops_cycle,
    run_ops_daemon,
)


class _FakeSettings:
    hiring_ops_sweep_running_timeout_min = 30
    hiring_ops_sweep_retrying_timeout_min = 120
    hiring_ops_reconcile_limit = 100
    hiring_alert_collector_types = ["HIRING"]
    hiring_alert_failure_rate_threshold = 0.5
    hiring_ops_interval_sec = 0.0
    discord_webhook_url = "https://discord.test/hook"


def _row(run_id, status, *, collected=10, inserted=10, skipped=0, failed=0):
    return {
        "id": run_id, "status": status, "collected_count": collected,
        "inserted_count": inserted, "skipped_count": skipped, "failed_count": failed,
    }


class AlertReasonTest(unittest.TestCase):
    def setUp(self):
        self.cfg = _FakeSettings()

    def test_healthy_run_no_alert(self):
        self.assertIsNone(_alert_reason(_row(1, "success", collected=10, inserted=10), self.cfg))

    def test_failed_status(self):
        self.assertIn("전건 실패", _alert_reason(_row(1, "failed"), self.cfg) or "")

    def test_rejection_rate_over_threshold(self):
        # 6/10 = 0.6 ≥ 0.5
        reason = _alert_reason(_row(1, "partial", collected=10, inserted=4, failed=6), self.cfg)
        self.assertIn("거부율", reason or "")

    def test_rejection_rate_under_threshold(self):
        # 3/10 = 0.3 < 0.5 → 정상
        self.assertIsNone(
            _alert_reason(_row(1, "partial", collected=10, inserted=7, failed=3), self.cfg)
        )

    def test_silent_failure(self):
        # success/partial 인데 수집은 됐으나 신규 0건
        reason = _alert_reason(_row(1, "success", collected=10, inserted=0, skipped=10), self.cfg)
        self.assertIn("침묵", reason or "")


class NewFinishedRunsTest(unittest.TestCase):
    def test_filters_running_and_old(self):
        rows = [_row(3, "running"), _row(2, "failed"), _row(1, "success")]
        result = _new_finished_runs(rows, since_id=1)
        self.assertEqual([r["id"] for r in result], [2])  # 3=running 제외, 1=old 제외


def _make_pool(conn):
    pool = mock.MagicMock()
    cm = mock.AsyncMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = False
    pool.acquire.return_value = cm
    return pool


class RunOpsCycleTest(unittest.IsolatedAsyncioTestCase):
    def _patches(self, rows):
        pqr = mock.patch("signal_alpha_data_access.repositories.ProcessingQueueRepository")
        dlr = mock.patch("signal_alpha_data_access.repositories.DeadLetterRepository")
        obs = mock.patch("signal_alpha_data_access.repositories.ObservabilityRepository")
        send = mock.patch.object(ops_daemon, "send_discord_alert", new=mock.AsyncMock(return_value=True))
        self.PQR, self.DLR, self.OBS = pqr.start(), dlr.start(), obs.start()
        self.send = send.start()
        self.addCleanup(mock.patch.stopall)
        self.PQR.return_value.sweep_stale_active_tasks = mock.AsyncMock(return_value={})
        self.DLR.return_value.reconcile_failed = mock.AsyncMock(return_value=0)
        self.OBS.return_value.recent_collector_runs = mock.AsyncMock(return_value=rows)

    async def test_self_healing_called_every_tick(self):
        self._patches([_row(1, "success")])
        seen = {"HIRING": 0}
        await run_ops_cycle(_make_pool(mock.Mock()), _FakeSettings(), mock.AsyncMock(), seen)
        self.PQR.return_value.sweep_stale_active_tasks.assert_awaited_once()
        self.DLR.return_value.reconcile_failed.assert_awaited_once()

    async def test_alerts_only_violating_new_run(self):
        self._patches([_row(2, "failed"), _row(1, "success")])
        seen = {"HIRING": 1}  # warm 상태
        await run_ops_cycle(_make_pool(mock.Mock()), _FakeSettings(), mock.AsyncMock(), seen)
        self.send.assert_awaited_once()  # id=2(failed)만 알림
        self.assertEqual(seen["HIRING"], 2)

    async def test_dedup_no_realert(self):
        self._patches([_row(2, "failed")])
        seen = {"HIRING": 2}  # 이미 본 run
        await run_ops_cycle(_make_pool(mock.Mock()), _FakeSettings(), mock.AsyncMock(), seen)
        self.send.assert_not_awaited()

    async def test_cold_start_warmup_suppresses_alerts(self):
        # seen 비어 있음(최초 틱) — 실패 run 이 있어도 알림 0회 + baseline 채움.
        self._patches([_row(2, "failed"), _row(1, "success")])
        seen: dict[str, int] = {}
        await run_ops_cycle(_make_pool(mock.Mock()), _FakeSettings(), mock.AsyncMock(), seen)
        self.send.assert_not_awaited()
        self.assertEqual(seen["HIRING"], 2)  # 최신 run_id 로 warm-up

    async def test_alerts_after_warmup(self):
        # warm-up 후 다음 틱에 새 위반 run 도착 → 알림.
        self._patches([_row(3, "failed"), _row(2, "failed")])
        seen = {"HIRING": 2}
        await run_ops_cycle(_make_pool(mock.Mock()), _FakeSettings(), mock.AsyncMock(), seen)
        self.send.assert_awaited_once()  # id=3만(2는 이미 본 것)


class DaemonCancelTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_propagates(self):
        with mock.patch.object(ops_daemon, "run_ops_cycle", new=mock.AsyncMock()):
            task = asyncio.create_task(run_ops_daemon(mock.MagicMock(), _FakeSettings()))
            await asyncio.sleep(0)  # 루프 진입
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    unittest.main()
