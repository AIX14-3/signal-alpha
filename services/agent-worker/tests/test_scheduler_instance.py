import asyncio
from datetime import datetime, time
from zoneinfo import ZoneInfo

import httpx
import signal_alpha_data_access.backend as backend

import run_scheduler_instance
from run_scheduler_instance import _fire, _next_run_at, _overall_status, _should_fire, run_cycle
from run_scheduler_instance import _backpressure_reason, _evaluate_schedule, _scheduler_dry_run


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

    async def get(self, url, *, headers, timeout):
        path = url.removeprefix("http://worker")
        self.posts.append(
            {
                "path": path,
                "json": None,
                "headers": headers,
                "timeout": timeout,
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
        async def get(self, url, *, headers, timeout):
            path = url.removeprefix("http://worker")
            self.posts.append(
                {
                    "path": path,
                    "json": None,
                    "headers": headers,
                    "timeout": timeout,
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
        }
    ]


def test_run_cycle_records_backpressure_skip_history(monkeypatch):
    repository = None

    class BacklogClient(RecordingClient):
        async def get(self, url, *, headers, timeout):
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
