"""PIT 신호 오버레이 리포지토리 — upsert·조회·거래구간 (fake 연결)."""

from __future__ import annotations

import asyncio
from datetime import date

from signal_alpha_data_access.repositories.user_trade_signal_overlays import (
    UserTradeSignalOverlayRepository,
)


class _FakeConn:
    def __init__(self):
        self.executed = []
        self.fetch_sql = None
        self.fetch_args = None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "INSERT 0 1"

    async def fetch(self, sql, *args):
        self.fetch_sql = sql
        self.fetch_args = args
        return []


def test_upsert_uses_natural_key_and_jsonb_cast():
    conn = _FakeConn()
    asyncio.run(
        UserTradeSignalOverlayRepository(conn).upsert_overlay(
            user_id=7, stock_id=10, ticker="005930", signal_date=date(2026, 6, 10),
            kind="insider_sell", detail='{"x":1}',
        )
    )
    sql = conn.executed[0][0]
    assert "ON CONFLICT (user_id, stock_id, signal_date, kind) DO UPDATE" in sql
    assert "$6::jsonb" in sql


def test_list_by_stock_scopes_user_and_stock():
    conn = _FakeConn()
    asyncio.run(UserTradeSignalOverlayRepository(conn).list_by_stock(user_id=7, stock_id=10))
    assert conn.fetch_args == (7, 10) and "ORDER BY signal_date" in conn.fetch_sql


def test_traded_ranges_filters_mapped_stocks():
    conn = _FakeConn()
    asyncio.run(UserTradeSignalOverlayRepository(conn).traded_stock_ranges())
    assert "stock_id IS NOT NULL" in conn.fetch_sql and "min(filled_at)" in conn.fetch_sql

    conn2 = _FakeConn()
    asyncio.run(UserTradeSignalOverlayRepository(conn2).traded_stock_ranges(user_id=7))
    assert conn2.fetch_args == (7,)
