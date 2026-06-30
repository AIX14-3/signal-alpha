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
    def __init__(self, columns_by_table=None):
        self.executed = []
        self.closed = False
        # _backend_columns 대역 — 테이블별 실존 컬럼. 비거나 미존재면 빈 결과(=백엔드에 테이블 없음).
        self._columns_by_table = columns_by_table or {}

    def transaction(self):
        return _FakeTxn()

    async def fetch(self, sql, table):
        # _backend_columns 조회 대역(pg_attribute). 테이블 미존재 시 빈 결과(M6: publisher 가 명시 에러).
        return [{"attname": c} for c in self._columns_by_table.get(table, [])]

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
    assert result["publish_status"] == "disabled"
    assert result["backend_database_configured"] is False
    assert "BACKEND_DATABASE_URL" in result["operator_hint"]


def test_publishes_to_backend_and_closes():
    source = _FakeSource({"stocks": [{"id": 1, "ticker": "005930"}]})
    backend = _FakeBackend(columns_by_table={"stocks": ["id", "ticker"]})

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


def test_missing_backend_table_raises_clear_error():
    # M6: 백엔드에 발행 대상 테이블이 없으면(컬럼 조회가 빔) 암호같은 INSERT 실패 대신
    # 테이블명을 담은 명확한 에러로 즉시 실패해야 한다(운영자가 백엔드 마이그를 적용하도록).
    source = _FakeSource({"stocks": [{"id": 1, "ticker": "005930"}]})
    backend = _FakeBackend(columns_by_table={})  # stocks 테이블 미존재

    async def connector(url):
        return backend

    handler = PublishSignalsTaskHandler(
        connection=source,
        settings=SimpleNamespace(backend_database_url="postgresql://backend/db"),
        backend_connector=connector,
    )

    try:
        asyncio.run(handler({"stock_id": 1, "task_context": {"stock_code": "005930"}}))
        raise AssertionError("expected RuntimeError for missing backend table")
    except RuntimeError as exc:
        assert "stocks" in str(exc)
    assert backend.closed is True  # 실패해도 연결은 정리
