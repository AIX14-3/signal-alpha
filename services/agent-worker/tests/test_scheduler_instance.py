import asyncio
import subprocess
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

import httpx
import pytest
import signal_alpha_data_access.backend as backend

import run_scheduler_instance
from run_scheduler_instance import _fire, _next_run_at, _overall_status, _should_fire, run_cycle
from run_scheduler_instance import _backpressure_reason, _evaluate_schedule, _scheduler_dry_run


@pytest.fixture(autouse=True)
def _reset_skip_suppression():
    # 스킵 히스토리 억제 상태는 모듈 전역(프로세스 로컬) — 테스트 간 누수 방지.
    run_scheduler_instance._last_skip_reason_by_schedule.clear()
    yield
    run_scheduler_instance._last_skip_reason_by_schedule.clear()


class FakeResponse:
    def __init__(self, payload, *, status_code=200, url="http://worker/test"):
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("POST", url)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code} error",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self):
        return self._payload


class RecordingClient:
    def __init__(self, *, failures=None):
        self.posts = []
        self.failures = set(failures or [])

    async def post(self, url, *, json, headers, timeout):
        path = url.removeprefix("http://worker")
        self.posts.append(
            {
                "path": path,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if path in self.failures:
            return FakeResponse({"error": "boom"}, status_code=500, url=url)
        if path == "/internal/schedules/dart/collect":
            return FakeResponse({"scheduled_count": 2}, url=url)
        if path == "/internal/schedules/report/collect":
            return FakeResponse({"scheduled_count": 3}, url=url)
        if path == "/internal/price/collect":
            return FakeResponse({"status": "ok"}, url=url)
        raise AssertionError(f"Unexpected path: {path}")

    async def get(self, url, *, headers, timeout, params=None):
        path = url.removeprefix("http://worker")
        self.posts.append(
            {
                "path": path,
                "json": None,
                "headers": headers,
                "timeout": timeout,
                "params": params,
            }
        )
        if path == "/internal/stats/queue":
            return FakeResponse({"totals_by_status": {}}, url=url)
        raise AssertionError(f"Unexpected path: {path}")


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return FakeAcquire(self.connection)


class FakeConnection:
    def __init__(self, *, lock_acquired=True):
        self.lock_acquired = lock_acquired
        self.fetchval_calls = []

    async def fetchval(self, sql, *args):
        self.fetchval_calls.append((sql, args))
        if "pg_try_advisory_lock" in sql:
            return self.lock_acquired
        if "pg_advisory_unlock" in sql:
            return True
        raise AssertionError(f"Unexpected fetchval SQL: {sql}")


class FakeScheduleRepository:
    def __init__(self, connection):
        self.connection = connection
        self.recorded_runs = []
        self.started_runs = []
        self.finished_runs = []

    async def get_by_name(self, name):
        return {
            "id": 1,
            "name": name,
            "enabled": True,
            "run_at_local": time(4, 30),
            "timezone": "Asia/Seoul",
            "targets": ["dart"],
            "dart_limit": 10,
            "price_modes": ["snapshot"],
            "last_run_at": None,
            "manual_trigger_requested_at": datetime(
                2026, 7, 1, 5, 0, tzinfo=ZoneInfo("Asia/Seoul")
            ),
        }

    async def get_by_id(self, schedule_id):
        # 락 획득 후 TOCTOU 재확인용 재조회 — 기본은 같은 행(레이스 없음)을 돌려준다.
        return await self.get_by_name("daily-collection")

    async def get_primary(self):
        raise AssertionError("primary schedule should not be fetched")

    async def list_all(self):
        raise AssertionError("all schedules should not be fetched")

    async def record_run(self, **kwargs):
        self.recorded_runs.append(kwargs)
        return kwargs

    async def start_run(self, **kwargs):
        self.started_runs.append(kwargs)
        return {"id": 55}

    async def finish_run(self, **kwargs):
        self.finished_runs.append(kwargs)
        return kwargs


def _interval_schedule(*, last_run_at=None):
    return {
        "id": 1,
        "name": "dart-collection",
        "enabled": True,
        "run_at_local": time(8, 30),
        "timezone": "Asia/Seoul",
        "targets": ["dart"],
        "dart_limit": 100,
        "price_modes": ["snapshot"],
        "frequency_minutes": 60,
        "active_from_local": time(8, 30),
        "active_until_local": time(20, 30),
        "last_run_at": last_run_at,
        "manual_trigger_requested_at": None,
    }


def test_interval_schedule_fires_after_frequency_inside_active_window():
    tz = ZoneInfo("Asia/Seoul")
    schedule = _interval_schedule(
        last_run_at=datetime(2026, 7, 1, 8, 30, tzinfo=tz)
    )

    assert _should_fire(schedule, datetime(2026, 7, 1, 9, 31, tzinfo=tz)) == (
        True,
        "scheduled",
    )


def test_interval_schedule_waits_until_frequency_elapsed():
    tz = ZoneInfo("Asia/Seoul")
    schedule = _interval_schedule(
        last_run_at=datetime(2026, 7, 1, 8, 30, tzinfo=tz)
    )

    assert _should_fire(schedule, datetime(2026, 7, 1, 9, 15, tzinfo=tz)) == (
        False,
        "not-due",
    )


def test_interval_schedule_waits_outside_active_window():
    tz = ZoneInfo("Asia/Seoul")
    schedule = _interval_schedule(
        last_run_at=datetime(2026, 7, 1, 19, 30, tzinfo=tz)
    )

    assert _should_fire(schedule, datetime(2026, 7, 1, 21, 0, tzinfo=tz)) == (
        False,
        "outside-window",
    )


def test_next_run_at_interval_schedule_rolls_to_next_window_after_cutoff():
    tz = ZoneInfo("Asia/Seoul")
    schedule = _interval_schedule()

    assert _next_run_at(datetime(2026, 7, 1, 20, 10, tzinfo=tz), schedule) == datetime(
        2026, 7, 2, 8, 30, tzinfo=tz
    )


def test_evaluate_schedule_returns_skip_decision_for_outside_active_window():
    tz = ZoneInfo("Asia/Seoul")
    schedule = _interval_schedule(
        last_run_at=datetime(2026, 7, 1, 19, 30, tzinfo=tz)
    )

    should_fire, decision = _evaluate_schedule(
        schedule,
        datetime(2026, 7, 1, 21, 0, tzinfo=tz),
    )

    assert should_fire is False
    assert decision == {
        "agent": "scheduler",
        "policy": "scheduler-agent-v1",
        "action": "skip",
        "reason": "outside-window",
        "schedule_id": 1,
        "schedule_name": "dart-collection",
        "targets": ["dart"],
    }


def test_backpressure_reason_detects_queue_backlog():
    reason = _backpressure_reason(
        {"totals_by_status": {"pending": 7, "retrying": 4, "failed": 0}},
        max_waiting=10,
        max_failed=5,
    )

    assert reason == "queue-backlog"


def test_fire_calls_report_collect_with_default_batch_payload(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    client = RecordingClient()

    summary = asyncio.run(
        _fire(
            client,
            base_url="http://worker",
            schedule={"targets": ["report"]},
        )
    )

    assert summary == {"report": 3}
    assert client.posts == [
        {
            "path": "/internal/schedules/report/collect",
            "json": {
                "limit": 100,
                "days_back": 7,
                "max_pages": 20,
                "priority": "batch",
            },
            "headers": {"X-Internal-Token": "secret"},
            "timeout": 120.0,
        }
    ]


def test_fire_uses_schedule_report_payload_over_defaults(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    client = RecordingClient()

    summary = asyncio.run(
        _fire(
            client,
            base_url="http://worker",
            schedule={
                "targets": ["report"],
                "report_limit": 12,
                "report_days_back": 3,
                "report_max_pages": 4,
            },
        )
    )

    assert summary == {"report": 3}
    assert client.posts == [
        {
            "path": "/internal/schedules/report/collect",
            "json": {
                "limit": 12,
                "days_back": 3,
                "max_pages": 4,
                "priority": "batch",
            },
            "headers": {"X-Internal-Token": "secret"},
            "timeout": 120.0,
        }
    ]


def test_fire_records_each_target_and_continues_after_report_failure(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    client = RecordingClient(failures={"/internal/schedules/report/collect"})

    summary = asyncio.run(
        _fire(
            client,
            base_url="http://worker/",
            schedule={
                "targets": ["dart", "report", "price"],
                "dart_limit": 5,
                "price_modes": ["snapshot"],
            },
        )
    )

    assert summary["dart"] == 2
    assert summary["report"].startswith("error: ")
    assert summary["price"] == {"snapshot": "ok"}
    assert [post["path"] for post in client.posts] == [
        "/internal/schedules/dart/collect",
        "/internal/schedules/report/collect",
        "/internal/price/collect",
    ]
    assert _overall_status(summary) == "partial"


def test_fire_runs_alternative_collect_and_analyze_commands(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    calls = []

    async def command_runner(argv, *, timeout):
        calls.append((argv, timeout))
        return {"returncode": 0, "stdout_tail": "ok", "stderr_tail": ""}

    summary = asyncio.run(
        _fire(
            RecordingClient(),
            base_url="http://worker",
            schedule={"targets": ["alternative"]},
            command_runner=command_runner,
        )
    )

    assert summary == {
        "alternative": {
            "collect": {"returncode": 0, "stdout_tail": "ok", "stderr_tail": ""},
            "analyze": {"returncode": 0, "stdout_tail": "ok", "stderr_tail": ""},
        }
    }
    assert [argv[-1] for argv, _timeout in calls] == [
        "run_collectors.py",
        "run_analyzers.py",
    ]


def test_fire_respects_alternative_policy_flags_and_timeouts(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    calls = []

    async def command_runner(argv, *, timeout):
        calls.append((argv, timeout))
        return {"returncode": 0, "stdout_tail": "ok", "stderr_tail": ""}

    summary = asyncio.run(
        _fire(
            RecordingClient(),
            base_url="http://worker",
            schedule={
                "targets": ["alternative"],
                "alternative_collect_enabled": False,
                "alternative_analyze_enabled": True,
                "alternative_analyze_timeout_seconds": 120,
            },
            command_runner=command_runner,
        )
    )

    assert summary == {
        "alternative": {
            "collect": "skipped: disabled",
            "analyze": {"returncode": 0, "stdout_tail": "ok", "stderr_tail": ""},
        }
    }
    assert calls == [([run_scheduler_instance.sys.executable, "run_analyzers.py"], 120)]


def test_fire_marks_alternative_partial_when_collect_fails_but_analyze_runs(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    calls = []

    async def command_runner(argv, *, timeout):
        calls.append(argv)
        if argv[-1] == "run_collectors.py":
            raise RuntimeError("collect failed")
        return {"returncode": 0, "stdout_tail": "analysis ok", "stderr_tail": ""}

    summary = asyncio.run(
        _fire(
            RecordingClient(),
            base_url="http://worker",
            schedule={"targets": ["alternative"]},
            command_runner=command_runner,
        )
    )

    assert summary["alternative"]["collect"] == "error: collect failed"
    assert summary["alternative"]["analyze"] == {
        "returncode": 0,
        "stdout_tail": "analysis ok",
        "stderr_tail": "",
    }
    assert [argv[-1] for argv in calls] == ["run_collectors.py", "run_analyzers.py"]
    assert _overall_status(summary) == "partial"


def test_scheduler_dry_run_reports_schedule_policy_skip_without_firing():
    tz = ZoneInfo("Asia/Seoul")
    schedule = _interval_schedule(last_run_at=datetime(2026, 7, 1, 8, 30, tzinfo=tz))
    schedule["backpressure_max_waiting"] = 5
    schedule["backpressure_max_failed"] = 10

    result = _scheduler_dry_run(
        schedule,
        now=datetime(2026, 7, 1, 9, 31, tzinfo=tz),
        queue_stats={"totals_by_status": {"pending": 6, "retrying": 0, "failed": 0}},
    )

    assert result["would_fire"] is False
    assert result["decision"] == {
        "agent": "scheduler",
        "policy": "scheduler-agent-v1",
        "action": "skip",
        "reason": "queue-backlog",
        "schedule_id": 1,
        "schedule_name": "dart-collection",
        "targets": ["dart"],
    }
    assert result["backpressure"] == {
        "reason": "queue-backlog",
        "max_waiting": 5,
        "max_failed": 10,
        "waiting": 6,
        "failed": 0,
    }
    assert result["next_run_at"] == "2026-07-01T10:31:00+09:00"


def test_scheduler_dry_run_manual_trigger_bypasses_backpressure():
    tz = ZoneInfo("Asia/Seoul")
    schedule = _interval_schedule(last_run_at=datetime(2026, 7, 1, 8, 30, tzinfo=tz))
    schedule["manual_trigger_requested_at"] = datetime(2026, 7, 1, 9, 0, tzinfo=tz)
    schedule["backpressure_max_waiting"] = 5

    result = _scheduler_dry_run(
        schedule,
        now=datetime(2026, 7, 1, 9, 31, tzinfo=tz),
        queue_stats={"totals_by_status": {"pending": 100, "retrying": 0, "failed": 0}},
    )

    assert result["would_fire"] is True
    assert result["decision"]["reason"] == "manual"
    assert result["backpressure"]["reason"] is None


def test_run_cycle_returns_lock_held_without_firing_when_scheduler_lock_is_taken(
    monkeypatch,
):
    repository = None

    def repo_factory(connection):
        nonlocal repository
        repository = FakeScheduleRepository(connection)
        return repository

    async def fire_should_not_run(*args, **kwargs):
        raise AssertionError("_fire must not run when advisory lock is held")

    monkeypatch.setattr(backend, "CollectionScheduleRepository", repo_factory)
    monkeypatch.setattr(run_scheduler_instance, "_fire", fire_should_not_run)

    connection = FakeConnection(lock_acquired=False)
    result = asyncio.run(
        run_cycle(
            FakePool(connection),
            object(),
            base_url="http://worker",
            schedule_name="daily-collection",
        )
    )

    assert result == "lock-held"
    assert repository is not None
    assert repository.recorded_runs == []
    assert any("pg_try_advisory_lock" in sql for sql, _args in connection.fetchval_calls)


def test_run_cycle_skips_scheduled_fire_when_queue_backlog_exceeds_limit(
    monkeypatch,
):
    repository = None

    class BacklogClient(RecordingClient):
        async def get(self, url, *, headers, timeout, params=None):
            path = url.removeprefix("http://worker")
            self.posts.append(
                {
                    "path": path,
                    "json": None,
                    "headers": headers,
                    "timeout": timeout,
                    "params": params,
                }
            )
            if path == "/internal/stats/queue":
                return FakeResponse(
                    {"totals_by_status": {"pending": 11, "retrying": 0, "failed": 0}},
                    url=url,
                )
            raise AssertionError(f"Unexpected path: {path}")

    class ScheduledRepository(FakeScheduleRepository):
        async def get_by_name(self, name):
            return {
                **await super().get_by_name(name),
                "run_at_local": time(0, 0),
                "last_run_at": None,
                "manual_trigger_requested_at": None,
            }

    def repo_factory(connection):
        nonlocal repository
        repository = ScheduledRepository(connection)
        return repository

    async def fire_should_not_run(*args, **kwargs):
        raise AssertionError("_fire must not run when queue backlog blocks the schedule")

    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    monkeypatch.setenv("SCHEDULER_BACKPRESSURE_MAX_WAITING", "10")
    monkeypatch.setattr(backend, "CollectionScheduleRepository", repo_factory)
    monkeypatch.setattr(run_scheduler_instance, "_fire", fire_should_not_run)

    client = BacklogClient()
    connection = FakeConnection(lock_acquired=True)
    result = asyncio.run(
        run_cycle(
            FakePool(connection),
            client,
            base_url="http://worker",
            schedule_name="daily-collection",
        )
    )

    assert result == "queue-backlog"
    assert repository is not None
    assert [run["trigger_reason"] for run in repository.started_runs] == ["queue-backlog"]
    assert [run["status"] for run in repository.finished_runs] == ["skipped"]
    assert client.posts == [
        {
            "path": "/internal/stats/queue",
            "json": None,
            "headers": {"X-Internal-Token": "secret"},
            "timeout": 30.0,
            "params": {"failed_window_minutes": 360},
        }
    ]


def test_run_cycle_records_backpressure_skip_history(monkeypatch):
    repository = None

    class BacklogClient(RecordingClient):
        async def get(self, url, *, headers, timeout, params=None):
            if url.removeprefix("http://worker") == "/internal/stats/queue":
                return FakeResponse(
                    {"totals_by_status": {"pending": 11, "retrying": 0, "failed": 0}},
                    url=url,
                )
            raise AssertionError(f"Unexpected URL: {url}")

    class ScheduledRepository(FakeScheduleRepository):
        async def get_by_name(self, name):
            return {
                **await super().get_by_name(name),
                "run_at_local": time(0, 0),
                "last_run_at": None,
                "manual_trigger_requested_at": None,
            }

    def repo_factory(connection):
        nonlocal repository
        repository = ScheduledRepository(connection)
        return repository

    async def fire_should_not_run(*args, **kwargs):
        raise AssertionError("_fire must not run when queue backlog blocks the schedule")

    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    monkeypatch.setenv("SCHEDULER_BACKPRESSURE_MAX_WAITING", "10")
    monkeypatch.setattr(backend, "CollectionScheduleRepository", repo_factory)
    monkeypatch.setattr(run_scheduler_instance, "_fire", fire_should_not_run)

    result = asyncio.run(
        run_cycle(
            FakePool(FakeConnection(lock_acquired=True)),
            BacklogClient(),
            base_url="http://worker",
            schedule_name="daily-collection",
        )
    )

    assert result == "queue-backlog"
    assert repository is not None
    assert repository.started_runs == [
        {
            "schedule_id": 1,
            "schedule_name": "daily-collection",
            "trigger_reason": "queue-backlog",
            "targets": ["dart"],
        }
    ]
    assert repository.finished_runs == [
        {
            "run_id": 55,
            "status": "skipped",
            "detail": {
                "decision": {
                    "agent": "scheduler",
                    "policy": "scheduler-agent-v1",
                    "action": "skip",
                    "reason": "queue-backlog",
                    "schedule_id": 1,
                    "schedule_name": "daily-collection",
                    "targets": ["dart"],
                },
                "targets": {},
            },
        }
    ]
    assert repository.recorded_runs == []


def test_run_cycle_records_execution_history_around_fired_schedule(monkeypatch):
    repository = None

    def repo_factory(connection):
        nonlocal repository
        repository = FakeScheduleRepository(connection)
        return repository

    async def fire_success(*args, **kwargs):
        return {"dart": 2}

    monkeypatch.setattr(backend, "CollectionScheduleRepository", repo_factory)
    monkeypatch.setattr(run_scheduler_instance, "_fire", fire_success)

    connection = FakeConnection(lock_acquired=True)
    result = asyncio.run(
        run_cycle(
            FakePool(connection),
            object(),
            base_url="http://worker",
            schedule_name="daily-collection",
        )
    )

    assert result == "fired:manual:ok"
    assert repository is not None
    assert repository.started_runs == [
        {
            "schedule_id": 1,
            "schedule_name": "daily-collection",
            "trigger_reason": "manual",
            "targets": ["dart"],
        }
    ]
    assert repository.finished_runs[0]["run_id"] == 55
    assert repository.finished_runs[0]["status"] == "ok"
    assert repository.finished_runs[0]["detail"]["targets"] == {"dart": 2}
    assert repository.recorded_runs[0]["last_status"] == "ok"
    assert any("pg_advisory_unlock" in sql for sql, _args in connection.fetchval_calls)


def test_run_cycle_records_scheduler_decision_in_execution_detail(monkeypatch):
    repository = None

    def repo_factory(connection):
        nonlocal repository
        repository = FakeScheduleRepository(connection)
        return repository

    async def fire_success(*args, **kwargs):
        return {"dart": 2}

    monkeypatch.setattr(backend, "CollectionScheduleRepository", repo_factory)
    monkeypatch.setattr(run_scheduler_instance, "_fire", fire_success)

    connection = FakeConnection(lock_acquired=True)
    result = asyncio.run(
        run_cycle(
            FakePool(connection),
            object(),
            base_url="http://worker",
            schedule_name="daily-collection",
        )
    )

    assert result == "fired:manual:ok"
    assert repository is not None
    detail = repository.finished_runs[0]["detail"]
    assert detail["targets"] == {"dart": 2}
    assert detail["decision"] == {
        "agent": "scheduler",
        "policy": "scheduler-agent-v1",
        "action": "fire",
        "reason": "manual",
        "schedule_id": 1,
        "schedule_name": "daily-collection",
        "targets": ["dart"],
    }
    assert repository.recorded_runs[0]["last_detail"] == detail


def test_run_cycle_finishes_execution_history_when_fire_raises(monkeypatch):
    repository = None

    def repo_factory(connection):
        nonlocal repository
        repository = FakeScheduleRepository(connection)
        return repository

    async def fire_failure(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(backend, "CollectionScheduleRepository", repo_factory)
    monkeypatch.setattr(run_scheduler_instance, "_fire", fire_failure)

    connection = FakeConnection(lock_acquired=True)
    result = asyncio.run(
        run_cycle(
            FakePool(connection),
            object(),
            base_url="http://worker",
            schedule_name="daily-collection",
        )
    )

    assert result == "fired:manual:partial"
    assert repository is not None
    assert repository.finished_runs[0]["run_id"] == 55
    assert repository.finished_runs[0]["status"] == "partial"
    assert repository.finished_runs[0]["detail"]["targets"] == {"error": "boom"}
    assert repository.recorded_runs[0]["last_status"] == "partial"
    assert any("pg_advisory_unlock" in sql for sql, _args in connection.fetchval_calls)


def test_run_cycle_fires_each_due_schedule_when_schedule_name_is_empty(monkeypatch):
    repository = None
    fired_schedule_names = []

    def due_schedule(schedule_id, name, targets):
        return {
            "id": schedule_id,
            "name": name,
            "enabled": True,
            "run_at_local": time(4, 30),
            "timezone": "Asia/Seoul",
            "targets": targets,
            "dart_limit": 10,
            "price_modes": ["snapshot"],
            "last_run_at": None,
            "manual_trigger_requested_at": datetime(
                2026, 7, 1, 5, 0, tzinfo=ZoneInfo("Asia/Seoul")
            ),
        }

    class MultiScheduleRepository(FakeScheduleRepository):
        async def get_by_name(self, name):
            raise AssertionError("named schedule should not be fetched")

        async def get_by_id(self, schedule_id):
            names = {1: ("price-collection", ["price"]), 2: ("report-collection", ["report"])}
            name, targets = names[schedule_id]
            return due_schedule(schedule_id, name, targets)

        async def get_primary(self):
            raise AssertionError("primary schedule should not be fetched")

        async def list_all(self):
            return [
                due_schedule(1, "price-collection", ["price"]),
                due_schedule(2, "report-collection", ["report"]),
            ]

    def repo_factory(connection):
        nonlocal repository
        repository = MultiScheduleRepository(connection)
        return repository

    async def fire_success(_client, *, base_url, schedule):
        fired_schedule_names.append(schedule["name"])
        return {schedule["targets"][0]: 1}

    monkeypatch.setattr(backend, "CollectionScheduleRepository", repo_factory)
    monkeypatch.setattr(run_scheduler_instance, "_fire", fire_success)

    connection = FakeConnection(lock_acquired=True)
    result = asyncio.run(
        run_cycle(
            FakePool(connection),
            object(),
            base_url="http://worker",
            schedule_name="",
        )
    )

    assert result == "fired:2:ok"
    assert fired_schedule_names == ["price-collection", "report-collection"]
    assert repository is not None
    assert [run["schedule_name"] for run in repository.started_runs] == [
        "price-collection",
        "report-collection",
    ]
    assert [run["schedule_id"] for run in repository.recorded_runs] == [1, 2]


# --- HIGH-1: 열린 인터벌 윈도우(active_until_local=NULL) 재발화 회귀 ---


def test_open_ended_interval_window_does_not_refire_every_poll():
    tz = ZoneInfo("Asia/Seoul")
    schedule = _interval_schedule(last_run_at=datetime(2026, 7, 1, 10, 0, tzinfo=tz))
    schedule["active_until_local"] = None  # 열린 윈도우

    # last_run 5분 전 — 윈도우 시작이 내일로 계산되면 last<window_start 가 항상 참이 되어
    # 매 폴(30s)마다 재발화한다(회귀 가드).
    assert _should_fire(schedule, datetime(2026, 7, 1, 10, 5, tzinfo=tz)) == (
        False,
        "not-due",
    )
    # frequency(60분) 경과 후에는 정상 발화.
    assert _should_fire(schedule, datetime(2026, 7, 1, 11, 1, tzinfo=tz)) == (
        True,
        "scheduled",
    )


# --- HIGH-2: failed backpressure 는 최근 윈도우 카운트(failed_recent) 사용 ---


def test_backpressure_uses_recent_failed_count_when_available():
    # 평생 누적 totals.failed 가 아무리 커도 최근 실패가 적으면 홀드하지 않는다.
    stats = {
        "totals_by_status": {"pending": 0, "retrying": 0, "failed": 5000},
        "failed_recent": 3,
        "failed_window_minutes": 360,
    }

    assert _backpressure_reason(stats, max_waiting=10, max_failed=100) is None


def test_backpressure_falls_back_to_lifetime_failed_totals_for_old_workers():
    # 롤링 배포 호환 — failed_recent 키가 없는 구버전 워커 응답은 기존 총계로 판단.
    stats = {"totals_by_status": {"pending": 0, "retrying": 0, "failed": 5000}}

    assert _backpressure_reason(stats, max_waiting=10, max_failed=100) == "recent-failures"


def test_fetch_queue_stats_sends_failed_window_minutes(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    monkeypatch.setenv("SCHEDULER_BACKPRESSURE_FAILED_WINDOW_MINUTES", "120")
    client = RecordingClient()

    asyncio.run(run_scheduler_instance._fetch_queue_stats(client, base_url="http://worker"))

    assert client.posts == [
        {
            "path": "/internal/stats/queue",
            "json": None,
            "headers": {"X-Internal-Token": "secret"},
            "timeout": 30.0,
            "params": {"failed_window_minutes": 120},
        }
    ]


# --- MED-4: waiting 적체는 스케줄 targets 관련 task_type 로 스코프 ---


def test_waiting_count_scopes_backlog_to_schedule_targets():
    stats = {
        "totals_by_status": {"pending": 903, "retrying": 2, "failed": 0},
        "items": [
            {"task_type": "collect_dart", "status": "pending", "count": 900},
            {"task_type": "analyze_price", "status": "pending", "count": 3},
            {"task_type": "NORMALIZE_HIRING", "status": "retrying", "count": 2},
            {"task_type": "collect_dart", "status": "success", "count": 50},
        ],
    }

    # DART 백로그(900)가 무관한 price 스케줄을 굶기지 않는다.
    assert _backpressure_reason(stats, max_waiting=10, max_failed=0, targets=["price"]) is None
    assert (
        _backpressure_reason(stats, max_waiting=10, max_failed=0, targets=["dart"])
        == "queue-backlog"
    )
    # alternative 는 대문자 task_type(HIRING/PATENT/DATALAB) 계열을 잡는다.
    assert run_scheduler_instance._waiting_count(stats, ["alternative"]) == 2
    # 매핑을 모르는 target 은 글로벌 총계로 보수적 폴백.
    assert run_scheduler_instance._waiting_count(stats, ["mystery"]) == 905
    # items 가 없는 구버전 응답도 글로벌 총계 폴백.
    assert run_scheduler_instance._waiting_count({"totals_by_status": {"pending": 7}}, ["dart"]) == 7


# --- MED-3: 락 획득 후 재확인(TOCTOU) — 레이스면 raced 로 스킵 ---


def test_run_cycle_skips_raced_schedule_when_recheck_after_lock_is_not_due(monkeypatch):
    repository = None

    class RacedRepository(FakeScheduleRepository):
        async def get_by_name(self, name):
            return {
                **await super().get_by_name(name),
                "run_at_local": time(0, 0),
                "last_run_at": None,
                "manual_trigger_requested_at": None,
            }

        async def get_by_id(self, schedule_id):
            # 락 획득 후 재조회 시점 — 다른 인스턴스가 이미 발화해 last_run_at 이 전진.
            return {
                **await self.get_by_name("daily-collection"),
                "last_run_at": datetime.now(ZoneInfo("Asia/Seoul")),
            }

    def repo_factory(connection):
        nonlocal repository
        repository = RacedRepository(connection)
        return repository

    async def fire_should_not_run(*args, **kwargs):
        raise AssertionError("_fire must not run when the schedule was raced")

    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    monkeypatch.setattr(backend, "CollectionScheduleRepository", repo_factory)
    monkeypatch.setattr(run_scheduler_instance, "_fire", fire_should_not_run)

    connection = FakeConnection(lock_acquired=True)
    result = asyncio.run(
        run_cycle(
            FakePool(connection),
            RecordingClient(),
            base_url="http://worker",
            schedule_name="daily-collection",
        )
    )

    assert result == "raced"
    assert repository is not None
    assert repository.recorded_runs == []
    assert [run["trigger_reason"] for run in repository.started_runs] == ["raced"]
    assert [run["status"] for run in repository.finished_runs] == ["skipped"]
    # 락은 잡았다 놓아야 한다.
    assert any("pg_advisory_unlock" in sql for sql, _args in connection.fetchval_calls)


# --- MED-5: 같은 사유의 연속 스킵 히스토리 억제 ---


def test_run_cycle_suppresses_repeated_backpressure_skip_history(monkeypatch):
    repositories = []

    class BacklogClient(RecordingClient):
        async def get(self, url, *, headers, timeout, params=None):
            if url.removeprefix("http://worker") == "/internal/stats/queue":
                return FakeResponse(
                    {"totals_by_status": {"pending": 11, "retrying": 0, "failed": 0}},
                    url=url,
                )
            raise AssertionError(f"Unexpected URL: {url}")

    class ScheduledRepository(FakeScheduleRepository):
        async def get_by_name(self, name):
            return {
                **await super().get_by_name(name),
                "run_at_local": time(0, 0),
                "last_run_at": None,
                "manual_trigger_requested_at": None,
            }

    def repo_factory(connection):
        repo = ScheduledRepository(connection)
        repositories.append(repo)
        return repo

    async def fire_should_not_run(*args, **kwargs):
        raise AssertionError("_fire must not run when queue backlog blocks the schedule")

    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    monkeypatch.setenv("SCHEDULER_BACKPRESSURE_MAX_WAITING", "10")
    monkeypatch.setattr(backend, "CollectionScheduleRepository", repo_factory)
    monkeypatch.setattr(run_scheduler_instance, "_fire", fire_should_not_run)

    def one_cycle():
        return asyncio.run(
            run_cycle(
                FakePool(FakeConnection(lock_acquired=True)),
                BacklogClient(),
                base_url="http://worker",
                schedule_name="daily-collection",
            )
        )

    # 매 폴(30s)마다 skip 히스토리가 쌓이면 하루 ~2880행 — 같은 사유는 첫 1회만 기록.
    assert one_cycle() == "queue-backlog"
    assert one_cycle() == "queue-backlog"
    assert one_cycle() == "queue-backlog"

    total_started = sum(len(repo.started_runs) for repo in repositories)
    total_finished = sum(len(repo.finished_runs) for repo in repositories)
    assert total_started == 1
    assert total_finished == 1
    # last_run_at 은 스킵에서 전진하지 않는다.
    assert all(repo.recorded_runs == [] for repo in repositories)


# --- MED-6: record_run 실패 시 1회 재시도, 그래도 실패면 히스토리 error ---


class _RecordFailRepository(FakeScheduleRepository):
    def __init__(self, connection, *, fail_times):
        super().__init__(connection)
        self.fail_times = fail_times
        self.record_attempts = 0

    async def record_run(self, **kwargs):
        self.record_attempts += 1
        if self.record_attempts <= self.fail_times:
            raise RuntimeError("db write failed for url ?crtfc_key=SECRET123&corp_code=1")
        return await super().record_run(**kwargs)


def _run_named_cycle(monkeypatch, repo_factory, fire):
    monkeypatch.setattr(backend, "CollectionScheduleRepository", repo_factory)
    monkeypatch.setattr(run_scheduler_instance, "_fire", fire)
    connection = FakeConnection(lock_acquired=True)
    result = asyncio.run(
        run_cycle(
            FakePool(connection),
            object(),
            base_url="http://worker",
            schedule_name="daily-collection",
        )
    )
    return result, connection


def test_run_cycle_marks_history_error_when_record_run_fails_twice(monkeypatch):
    repository = None

    def repo_factory(connection):
        nonlocal repository
        repository = _RecordFailRepository(connection, fail_times=2)
        return repository

    async def fire_success(*args, **kwargs):
        return {"dart": 2}

    result, _connection = _run_named_cycle(monkeypatch, repo_factory, fire_success)

    # 재시도까지 실패 — last_run_at 이 전진하지 못했으니 히스토리는 "ok" 가 아니라
    # "error" + 예외 내용으로 남는다(다음 사이클 중복 발화 추적 근거).
    assert result == "fired:manual:error"
    assert repository is not None
    assert repository.record_attempts == 2
    assert repository.recorded_runs == []
    finished = repository.finished_runs[0]
    assert finished["status"] == "error"
    assert "record_run_error" in finished["detail"]
    assert "crtfc_key=***" in finished["detail"]["record_run_error"]
    assert "SECRET123" not in finished["detail"]["record_run_error"]


def test_run_cycle_retries_record_run_once_and_keeps_ok(monkeypatch):
    repository = None

    def repo_factory(connection):
        nonlocal repository
        repository = _RecordFailRepository(connection, fail_times=1)
        return repository

    async def fire_success(*args, **kwargs):
        return {"dart": 2}

    result, _connection = _run_named_cycle(monkeypatch, repo_factory, fire_success)

    assert result == "fired:manual:ok"
    assert repository is not None
    assert repository.record_attempts == 2
    assert len(repository.recorded_runs) == 1
    assert repository.finished_runs[0]["status"] == "ok"


# --- MED-7: 스케줄별 advisory lock 키(hashtext(name)) ---


def test_run_cycle_uses_per_schedule_advisory_lock_key(monkeypatch):
    def repo_factory(connection):
        return FakeScheduleRepository(connection)

    async def fire_success(*args, **kwargs):
        return {"dart": 2}

    monkeypatch.setattr(backend, "CollectionScheduleRepository", repo_factory)
    monkeypatch.setattr(run_scheduler_instance, "_fire", fire_success)

    connection = FakeConnection(lock_acquired=True)
    asyncio.run(
        run_cycle(
            FakePool(connection),
            object(),
            base_url="http://worker",
            schedule_name="daily-collection",
        )
    )

    lock_calls = [
        (sql, args) for sql, args in connection.fetchval_calls if "advisory" in sql
    ]
    assert [args for _sql, args in lock_calls] == [
        (run_scheduler_instance._SCHEDULER_ADVISORY_LOCK_CLASS, "daily-collection"),
        (run_scheduler_instance._SCHEDULER_ADVISORY_LOCK_CLASS, "daily-collection"),
    ]
    # SQL 쪽 안정 해시(hashtext) 2-인자형 — 이름이 다르면 서로 블록하지 않는다.
    assert all("hashtext($2)" in sql for sql, _args in lock_calls)


# --- MED-8: 결정 히스토리 시크릿 마스킹 ---


def test_redact_masks_sensitive_query_param_values():
    text = (
        "command failed: https://opendart.fss.or.kr/api/list.json?"
        "crtfc_key=abcd1234&corp_code=00126380 appkey=AA secretkey=BB token=CC monkey=safe"
    )

    redacted = run_scheduler_instance._redact(text)

    assert "crtfc_key=***" in redacted
    assert "abcd1234" not in redacted
    assert "corp_code=00126380" in redacted  # 민감하지 않은 파라미터는 보존
    assert "appkey=***" in redacted
    assert "secretkey=***" in redacted
    assert "token=***" in redacted
    assert "monkey=safe" in redacted  # 단어 중간의 key 는 오탐하지 않는다


def test_fire_redacts_secrets_in_alternative_error_summary(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")

    async def command_runner(argv, *, timeout):
        raise RuntimeError("boom: GET ?crtfc_key=TOPSECRET failed")

    summary = asyncio.run(
        _fire(
            RecordingClient(),
            base_url="http://worker",
            schedule={"targets": ["alternative"], "alternative_analyze_enabled": False},
            command_runner=command_runner,
        )
    )

    assert summary["alternative"]["collect"] == "error: boom: GET ?crtfc_key=*** failed"


# --- MED-9: 명시된 SCHEDULE_NAME 미존재 시 primary 폴백 금지 ---


def test_run_cycle_does_not_fall_back_to_primary_for_unknown_schedule_name(monkeypatch):
    repository = None

    class MissingRepository(FakeScheduleRepository):
        async def get_by_name(self, name):
            return None

        # get_primary 는 부모가 AssertionError — 폴백 호출 시 테스트가 즉시 실패한다.

    def repo_factory(connection):
        nonlocal repository
        repository = MissingRepository(connection)
        return repository

    async def fire_should_not_run(*args, **kwargs):
        raise AssertionError("_fire must not run for an unknown schedule name")

    monkeypatch.setattr(backend, "CollectionScheduleRepository", repo_factory)
    monkeypatch.setattr(run_scheduler_instance, "_fire", fire_should_not_run)

    result = asyncio.run(
        run_cycle(
            FakePool(FakeConnection()),
            object(),
            base_url="http://worker",
            schedule_name="typo-name",
        )
    )

    assert result == "schedule-not-found"
    assert repository is not None
    assert repository.started_runs == []
    assert repository.recorded_runs == []


# --- LOW-10: _overall_status 는 구조화 필드만 검사(repr 부분 문자열 금지) ---


def test_overall_status_ignores_error_text_inside_command_output_tails():
    summary = {
        "alternative": {
            "collect": {
                "returncode": 0,
                "stdout_tail": "collector log: error: transient fetch retried; done",
                "stderr_tail": "",
            },
            "analyze": "skipped: disabled",
        }
    }

    assert _overall_status(summary) == "ok"


def test_overall_status_flags_nonzero_returncode_as_partial():
    summary = {
        "alternative": {
            "collect": {"returncode": 1, "stdout_tail": "", "stderr_tail": ""},
            "analyze": "skipped: disabled",
        }
    }

    assert _overall_status(summary) == "partial"


# --- LOW-14: 모듈 import 는 부작용(로깅 설정 등)이 없어야 한다 ---


def test_import_has_no_logging_side_effects():
    # 워커가 dry-run 라우트(app/api/routes/schedules.py)에서 import 하므로,
    # import 시 logging.basicConfig/bootstrap 이 실행되면 워커 프로세스를 오염시킨다.
    code = (
        "import logging; "
        "before = list(logging.getLogger().handlers); "
        "import run_scheduler_instance; "
        "assert list(logging.getLogger().handlers) == before, 'import configured logging'; "
        "print('side-effect-free')"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=run_scheduler_instance._SERVICE_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert "side-effect-free" in completed.stdout
