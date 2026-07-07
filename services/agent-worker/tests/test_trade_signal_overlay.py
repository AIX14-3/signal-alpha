"""PIT 신호 오버레이 갱신 — dart 이중풀·멱등·격리 (fake 연결)."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from decimal import Decimal

from app.publish.trade_signal_overlay import refresh_signal_overlays


class _FakeBackend:
    def __init__(self, ranges):
        self._ranges = ranges
        self.upserts = []  # (user_id, stock_id, ticker, signal_date, kind, detail)

    async def fetch(self, sql, *args):
        assert "min(filled_at)" in sql  # traded_stock_ranges
        return self._ranges

    async def execute(self, sql, *args):
        assert "INSERT INTO user_trade_signal_overlays" in sql
        assert "ON CONFLICT (user_id, stock_id, signal_date, kind) DO UPDATE" in sql
        self.upserts.append(args)
        return "INSERT 0 1"


class _FakeSource:
    def __init__(self, events_by_stock):
        self._events = events_by_stock
        self.queried = []

    async def fetch(self, sql, stock_id, start, end):
        assert "FROM dart_ownership_events" in sql and "report_date" in sql
        self.queried.append((stock_id, start, end))
        return self._events.get(stock_id, [])


def _range(user_id, stock_id, ticker, start, end):
    return {
        "user_id": user_id, "stock_id": stock_id, "ticker": ticker,
        "start_date": date.fromisoformat(start), "end_date": date.fromisoformat(end),
    }


def _event(day, delta):
    return {
        "report_date": date(2026, 6, day), "holder_type": "executive",
        "holder_name": "홍길동", "shares_delta": Decimal(delta), "ratio_delta": Decimal("0.5"),
    }


def test_maps_sell_and_buy_and_upserts():
    backend = _FakeBackend([_range(7, 10, "005930", "2026-06-05", "2026-06-30")])
    source = _FakeSource({10: [_event(10, "-1000"), _event(20, "500")]})

    stats = asyncio.run(refresh_signal_overlays(backend, source))

    assert stats.stocks == 1 and stats.signals == 2 and stats.failed == 0
    kinds = [a[4] for a in backend.upserts]
    assert kinds == ["insider_sell", "insider_buy"]
    # detail 은 jsonb 문자열
    detail = json.loads(backend.upserts[0][5])
    assert detail["shares_delta"] == "-1000" and detail["holder_type"] == "executive"


def test_lookback_extends_query_start_before_trade():
    backend = _FakeBackend([_range(7, 10, "005930", "2026-06-05", "2026-06-30")])
    source = _FakeSource({10: []})
    asyncio.run(refresh_signal_overlays(backend, source))
    # 조회 시작 = 거래 시작(6/5) - 30일 = 5/6
    stock_id, start, end = source.queried[0]
    assert start == date(2026, 5, 6) and end == date(2026, 6, 30)


def test_isolates_failing_stock():
    class _Boom(_FakeSource):
        async def fetch(self, sql, stock_id, start, end):
            if stock_id == 99:
                raise RuntimeError("dart down")
            return await super().fetch(sql, stock_id, start, end)

    backend = _FakeBackend(
        [_range(7, 99, "X", "2026-06-01", "2026-06-30"),
         _range(7, 10, "005930", "2026-06-01", "2026-06-30")]
    )
    source = _Boom({10: [_event(10, "-1")]})

    stats = asyncio.run(refresh_signal_overlays(backend, source))

    assert stats.failed == 1 and stats.stocks == 1 and stats.signals == 1
    assert any("stock=99" in e for e in stats.errors)
