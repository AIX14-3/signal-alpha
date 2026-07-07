"""매매 계획 리포지토리 — upsert·조회·삭제 (fake 연결)."""

from __future__ import annotations

import asyncio

from signal_alpha_data_access.repositories.user_trade_plans import UserTradePlanRepository


class _FakeConn:
    def __init__(self, *, row=None, exec_result="DELETE 1"):
        self._row = row
        self._exec_result = exec_result
        self.fetchrow_sql = None
        self.args = None
        self.executed = []

    async def fetchrow(self, sql, *args):
        self.fetchrow_sql = sql
        self.args = args
        return self._row or {
            "id": 1, "stock_id": args[1] if "INSERT" in sql else 5, "ticker": "005930",
            "thesis": "t", "target_price": 100, "stop_price": 90, "sell_condition": None,
            "planned_at": None, "created_at": None, "updated_at": None,
        }

    async def fetch(self, sql, *args):
        self.args = args
        return []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return self._exec_result


def test_upsert_uses_conflict_on_user_ticker():
    conn = _FakeConn()
    row = asyncio.run(
        UserTradePlanRepository(conn).upsert_plan(
            user_id=7, stock_id=5, ticker="005930", thesis="t",
            target_price=100, stop_price=90, sell_condition=None,
        )
    )
    assert "ON CONFLICT (user_id, ticker) DO UPDATE" in conn.fetchrow_sql
    assert "RETURNING" in conn.fetchrow_sql
    assert row["ticker"] == "005930"


def test_get_plan_passes_user_and_ticker():
    conn = _FakeConn(row={"id": 1, "ticker": "005930"})
    out = asyncio.run(UserTradePlanRepository(conn).get_plan(user_id=7, ticker="005930"))
    assert conn.args == (7, "005930") and out["ticker"] == "005930"


def test_delete_returns_bool():
    hit = _FakeConn(exec_result="DELETE 1")
    miss = _FakeConn(exec_result="DELETE 0")
    assert asyncio.run(UserTradePlanRepository(hit).delete_plan(user_id=7, ticker="X")) is True
    assert asyncio.run(UserTradePlanRepository(miss).delete_plan(user_id=7, ticker="X")) is False
