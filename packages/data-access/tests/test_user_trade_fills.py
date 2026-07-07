"""체결내역 리포지토리 — 멱등 upsert·증분 커서·매핑·조회·삭제 (fake 연결)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from signal_alpha_data_access.repositories.user_trade_fills import UserTradeFillsRepository


class _FakeConn:
    def __init__(self, *, exec_result="INSERT 0 1", last=None, stock_id=1):
        self._exec_result = exec_result
        self._last = last
        self._stock_id = stock_id
        self.fetch_sql = None
        self.fetch_args = None
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return self._exec_result

    async def fetchval(self, sql, *args):
        if "max(filled_at)" in sql:
            return self._last
        if "FROM stocks WHERE ticker" in sql:
            return self._stock_id
        return None

    async def fetch(self, sql, *args):
        self.fetch_sql = sql
        self.fetch_args = args
        return []


_FILL = dict(
    user_id=7, broker="kiwoom", account_ref="A", broker_fill_id="C1", stock_id=1,
    ticker="005930", side="buy", filled_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    quantity=10, price=70000, fee=None,
)


def test_upsert_returns_true_on_insert_false_on_conflict():
    hit = _FakeConn(exec_result="INSERT 0 1")
    conflict = _FakeConn(exec_result="INSERT 0 0")
    assert asyncio.run(UserTradeFillsRepository(hit).upsert_fill(**_FILL)) is True
    assert asyncio.run(UserTradeFillsRepository(conflict).upsert_fill(**_FILL)) is False
    # ON CONFLICT DO NOTHING 사용(체결 불변)
    assert "ON CONFLICT (user_id, broker, broker_fill_id) DO NOTHING" in hit.executed[0][0]


def test_last_filled_at_cursor():
    ts = datetime(2026, 7, 5, tzinfo=timezone.utc)
    conn = _FakeConn(last=ts)
    out = asyncio.run(UserTradeFillsRepository(conn).last_filled_at(user_id=7, broker="kiwoom"))
    assert out == ts


def test_resolve_stock_id_hit_and_miss():
    hit = _FakeConn(stock_id=42)
    miss = _FakeConn(stock_id=None)
    assert asyncio.run(UserTradeFillsRepository(hit).resolve_stock_id(ticker="005930")) == 42
    assert asyncio.run(UserTradeFillsRepository(miss).resolve_stock_id(ticker="ZZZ")) is None


def test_list_fills_branches_on_stock_id():
    conn = _FakeConn()
    asyncio.run(UserTradeFillsRepository(conn).list_fills(user_id=7, stock_id=5, limit=100))
    assert "stock_id = $2" in conn.fetch_sql and conn.fetch_args == (7, 5, 100)

    conn2 = _FakeConn()
    asyncio.run(UserTradeFillsRepository(conn2).list_fills(user_id=7, limit=100))
    assert "stock_id" not in conn2.fetch_sql.split("WHERE", 1)[1] and conn2.fetch_args == (7, 100)


def test_delete_for_user_counts():
    conn = _FakeConn(exec_result="DELETE 4")
    n = asyncio.run(UserTradeFillsRepository(conn).delete_for_user(user_id=7))
    assert n == 4
    conn2 = _FakeConn(exec_result="DELETE 2")
    n2 = asyncio.run(UserTradeFillsRepository(conn2).delete_for_user(user_id=7, broker="toss"))
    assert n2 == 2 and "broker = $2" in conn2.executed[0][0]
