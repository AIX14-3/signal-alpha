"""PUBLISH_SIGNALS 핸들러 (#11) — 게이트(단일 DB no-op) + 백엔드 발행(fake)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.publish.publish_task import PublishSignalsTaskHandler


class _FakeSource:
    def __init__(self, rows_by_table):
        self._rows = rows_by_table

    async def fetch(self, sql, stock_id):
        for table, rows in self._rows.items():
            if f"FROM {table} " in sql:
                return rows
        return []


class _FakeTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeBackend:
    def __init__(self):
        self.executed = []
        self.closed = False

    def transaction(self):
        return _FakeTxn()

    async def executemany(self, sql, args_list):
        self.executed.append((sql, args_list))

    async def close(self):
        self.closed = True


def test_no_backend_url_is_noop():
    handler = PublishSignalsTaskHandler(
        connection=_FakeSource({}),
        settings=SimpleNamespace(backend_database_url=None),
    )
    result = asyncio.run(handler({"stock_id": 7}))
    assert result["skipped_reason"] == "no_backend_db"


def test_publishes_to_backend_and_closes():
    source = _FakeSource({"stocks": [{"id": 1, "ticker": "005930"}]})
    backend = _FakeBackend()

    async def connector(url):
        assert url == "postgresql://backend/db"
        return backend

    handler = PublishSignalsTaskHandler(
        connection=source,
        settings=SimpleNamespace(backend_database_url="postgresql://backend/db"),
        backend_connector=connector,
    )

    result = asyncio.run(handler({"stock_id": 1, "task_context": {"stock_code": "005930"}}))

    assert result["published"]["stocks"] == 1
    assert backend.executed  # stocks 발행됨
    assert backend.closed is True  # 연결 정리
