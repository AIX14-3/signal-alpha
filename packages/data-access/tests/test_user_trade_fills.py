"""체결내역 리포지토리 — 수기 입력(insert)·매핑·조회·삭제 (fake 연결)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from signal_alpha_data_access.repositories.user_trade_fills import UserTradeFillsRepository


class _FakeConn:
    def __init__(self, *, exec_result="DELETE 1", row=None):
        self._exec_result = exec_result
        self._row = row if row is not None else {"id": 1}
        self.fetch_sql = None
        self.fetch_args = None
        self.fetchrow_sql = None
        self.fetchrow_args = None
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return self._exec_result

    async def fetchrow(self, sql, *args):
        self.fetchrow_sql = sql
        self.fetchrow_args = args
        return self._row

    async def fetch(self, sql, *args):
        self.fetch_sql = sql
        self.fetch_args = args
        return []


_FILL = dict(
    user_id=7, stock_id=1, ticker="005930", side="buy",
    filled_at=datetime(2026, 7, 1, tzinfo=timezone.utc), quantity=10, price=70000, fee=None,
)


def test_insert_fill_returns_row_and_has_no_broker_columns():
    conn = _FakeConn(row={"id": 42})
    out = asyncio.run(UserTradeFillsRepository(conn).insert_fill(**_FILL))
    assert out == {"id": 42}
    # 수기 입력 — 브로커/자연키 컬럼 없이 plain INSERT + RETURNING.
    assert "INSERT INTO user_trade_fills" in conn.fetchrow_sql
    assert "broker" not in conn.fetchrow_sql
    assert "RETURNING" in conn.fetchrow_sql


def test_list_fills_branches_on_stock_id():
    conn = _FakeConn()
    asyncio.run(UserTradeFillsRepository(conn).list_fills(user_id=7, stock_id=5, limit=100))
    assert "stock_id = $2" in conn.fetch_sql and conn.fetch_args == (7, 5, 100)

    conn2 = _FakeConn()
    asyncio.run(UserTradeFillsRepository(conn2).list_fills(user_id=7, limit=100))
    assert "stock_id" not in conn2.fetch_sql.split("WHERE", 1)[1] and conn2.fetch_args == (7, 100)


def test_delete_fill_hit_and_miss():
    hit = _FakeConn(exec_result="DELETE 1")
    miss = _FakeConn(exec_result="DELETE 0")
    assert asyncio.run(UserTradeFillsRepository(hit).delete_fill(user_id=7, fill_id=1)) is True
    assert asyncio.run(UserTradeFillsRepository(miss).delete_fill(user_id=7, fill_id=9)) is False
    assert "WHERE user_id = $1 AND id = $2" in hit.executed[0][0]


def test_delete_for_user_counts():
    conn = _FakeConn(exec_result="DELETE 4")
    n = asyncio.run(UserTradeFillsRepository(conn).delete_for_user(user_id=7))
    assert n == 4
