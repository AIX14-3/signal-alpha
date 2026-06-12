"""runner.py: run_once 두 모드, supervise 재기동/cancel 전파, cancel 시 세션 마감."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

import app.collectors.price.runner as runner
from app.collectors.price.market_hours import KST
from app.collectors.price.schemas import TargetStock
from tests.price_collector.fakes import (
    FakeRepository,
    FakeRestClient,
    ka10001_payload,
    ka10059_payload,
)

TARGETS = [TargetStock(stock_id=1, ticker="005930", name="삼성전자")]


def make_settings(**overrides):
    base = dict(
        kiwoom_app_key="key",
        kiwoom_app_secret="secret",
        kiwoom_api_base="https://mockapi.kiwoom.com",
        kiwoom_timeout_seconds=1.0,
        kiwoom_min_request_interval_sec=0.0,
        price_poll_interval_sec=0.01,
        price_flow_delay_after_close_min=30,
        market_open="09:00",
        market_close="15:30",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeCollectionRuns:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []
        self.finished: list[tuple[int, str]] = []

    async def create_collector_run(self, collector_type: str, run_mode: str) -> int:
        self.created.append((collector_type, run_mode))
        return len(self.created)

    async def finish_collector_run(
        self,
        *,
        run_id: int,
        status: str,
        collected_count: int,
        inserted_count: int,
        skipped_count: int,
        failed_count: int,
        error_message: str | None,
    ) -> None:
        self.finished.append((run_id, status))


class _FakeAcquire:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc) -> bool:
        return False


class FakeLockConnection:
    """pg_try_advisory_lock 호출에 고정 응답하는 커넥션 더블."""

    def __init__(self, locked: bool) -> None:
        self.locked = locked
        self.calls = 0

    async def fetchval(self, query: str, *args):
        self.calls += 1
        return self.locked


class FakeLockPool:
    def __init__(self, locked: bool = True) -> None:
        self.connection = FakeLockConnection(locked)

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.connection)


def patch_runner(monkeypatch, repo, runs, client) -> None:
    monkeypatch.setattr(runner, "PriceSnapshotRepository", lambda pool: repo)
    monkeypatch.setattr(runner, "CollectionRepository", lambda pool: runs)
    monkeypatch.setattr(runner, "build_client", lambda settings, http: client)


def test_run_once_snapshot_mode(monkeypatch):
    client = FakeRestClient()
    client.payloads[("ka10001", "005930")] = ka10001_payload()
    repo = FakeRepository(TARGETS)
    runs = FakeCollectionRuns()
    patch_runner(monkeypatch, repo, runs, client)

    stats = asyncio.run(runner.run_once(None, make_settings(), flows_only=False))

    assert stats.collected == 1
    assert stats.stored == 1
    assert len(repo.snapshots) == 1
    assert runs.created == [("PRICE", "manual")]
    assert runs.finished == [(1, "success")]


def test_run_once_flows_mode(monkeypatch):
    fixed = datetime(2026, 6, 11, 16, 30, tzinfo=KST)
    monkeypatch.setattr(runner, "now_kst", lambda: fixed)
    client = FakeRestClient()
    client.payloads[("ka10059", "005930")] = ka10059_payload()
    repo = FakeRepository(TARGETS)
    repo.ohlcv_rows.add((1, fixed.date()))
    runs = FakeCollectionRuns()
    patch_runner(monkeypatch, repo, runs, client)

    stats = asyncio.run(runner.run_once(None, make_settings(), flows_only=True))

    assert stats.stored == 1
    assert repo.flow_updates[0][1].foreign_net == 95000
    assert runs.created == [("PRICE", "manual")]
    assert runs.finished == [(1, "success")]


def test_run_once_without_targets_creates_no_run(monkeypatch):
    repo = FakeRepository([])
    runs = FakeCollectionRuns()
    patch_runner(monkeypatch, repo, runs, FakeRestClient())

    stats = asyncio.run(runner.run_once(None, make_settings(), flows_only=False))

    assert stats.collected == 0
    assert runs.created == []


def test_run_once_finishes_run_even_when_cycle_raises(monkeypatch):
    repo = FakeRepository(TARGETS)
    runs = FakeCollectionRuns()
    patch_runner(monkeypatch, repo, runs, FakeRestClient())

    async def boom(*args, **kwargs):
        raise RuntimeError("kiwoom exploded")

    monkeypatch.setattr(runner, "run_snapshot_cycle", boom)

    with pytest.raises(RuntimeError):
        asyncio.run(runner.run_once(None, make_settings(), flows_only=False))

    # 예외로 빠져나가도 collector_runs 행이 'running'으로 남지 않는다.
    assert runs.finished == [(1, "failed")]


def test_supervise_restarts_after_crash(monkeypatch):
    async def scenario():
        calls = 0
        second_call = asyncio.Event()

        async def fake_daemon(pool, settings):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("boom")
            second_call.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(runner, "run_daemon", fake_daemon)
        monkeypatch.setattr(runner, "_RESTART_DELAY_SEC", 0.01)

        task = asyncio.create_task(runner.supervise_daemon(FakeLockPool(), make_settings()))
        await asyncio.wait_for(second_call.wait(), timeout=2.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == 2

    asyncio.run(scenario())


def test_supervise_propagates_cancel(monkeypatch):
    async def scenario():
        started = asyncio.Event()

        async def fake_daemon(pool, settings):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(runner, "run_daemon", fake_daemon)

        task = asyncio.create_task(runner.supervise_daemon(FakeLockPool(), make_settings()))
        await asyncio.wait_for(started.wait(), timeout=2.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_supervise_waits_when_advisory_lock_is_held(monkeypatch):
    async def scenario():
        daemon_calls = 0

        async def fake_daemon(pool, settings):
            nonlocal daemon_calls
            daemon_calls += 1

        monkeypatch.setattr(runner, "run_daemon", fake_daemon)
        monkeypatch.setattr(runner, "_RESTART_DELAY_SEC", 0.01)
        pool = FakeLockPool(locked=False)

        task = asyncio.create_task(runner.supervise_daemon(pool, make_settings()))
        while pool.connection.calls < 3:  # 락 획득 재시도가 반복되는지 확인
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert daemon_calls == 0  # 락을 못 잡으면 폴링하지 않는다

    asyncio.run(scenario())


def test_daemon_cancel_finishes_open_session(monkeypatch):
    async def scenario():
        client = FakeRestClient()
        client.payloads[("ka10001", "005930")] = ka10001_payload()
        repo = FakeRepository(TARGETS)
        runs = FakeCollectionRuns()
        patch_runner(monkeypatch, repo, runs, client)
        monkeypatch.setattr(runner, "is_market_open", lambda now, open_t, close_t: True)

        task = asyncio.create_task(runner.run_daemon(None, make_settings()))
        while not runs.created:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert runs.created == [("PRICE", "batch")]
        assert runs.finished == [(1, "success")]

    asyncio.run(scenario())
