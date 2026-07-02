"""저널 outcome 확정 로직 — 거래일 산정/휴장 스킵/멱등 upsert (fake 연결)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime

from app.publish.journal_outcomes import (
    HORIZONS,
    record_journal_outcomes,
    resolve_outcome,
    sync_journal_chart_prices,
)


def _series(*points):
    return [(date.fromisoformat(d), close) for d, close in points]


# 2026-06-22(월)부터 평일만 12거래일 — 주말(6/27,28, 7/4,5) 갭 포함.
_TRADING_DATES = [
    "2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25", "2026-06-26",
    "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03",
    "2026-07-06", "2026-07-07",
]
_FULL_SERIES = _series(*[(d, 70000 + i * 100) for i, d in enumerate(_TRADING_DATES)])


def test_horizons_match_db_check_constraint():
    assert HORIZONS == (("7td", 7), ("30td", 30))


def test_resolve_outcome_picks_nth_trading_day():
    # 작성일 = 6/22(거래일) → base 6/22(70000), 7 거래일 후 = 7/01... 아님 —
    # base 인덱스 0 + 7 = 인덱스 7(2026-07-01, 70700).
    outcome = resolve_outcome(_FULL_SERIES, created_date=date(2026, 6, 22), trading_days=7)
    assert outcome["base_trade_date"] == date(2026, 6, 22)
    assert outcome["base_price"] == 70000
    assert outcome["outcome_trade_date"] == date(2026, 7, 1)
    assert outcome["outcome_price"] == 70700
    assert outcome["change_pct"] == 1.0


def test_resolve_outcome_weekend_created_uses_previous_trading_day():
    # 작성일 = 6/27(토, 휴장) → base 는 직전 거래일 6/26. target = 6/26 + 7거래일 = 7/07.
    outcome = resolve_outcome(_FULL_SERIES, created_date=date(2026, 6, 27), trading_days=7)
    assert outcome["base_trade_date"] == date(2026, 6, 26)
    assert outcome["outcome_trade_date"] == date(2026, 7, 7)


def test_resolve_outcome_pending_until_target_matures():
    assert resolve_outcome(_FULL_SERIES, created_date=date(2026, 6, 22), trading_days=30) == "pending"
    assert resolve_outcome(_FULL_SERIES, created_date=date(2026, 7, 3), trading_days=7) == "pending"


def test_resolve_outcome_skips_when_no_base_before_creation():
    # 작성일이 시계열 시작 이전 — base 산정 불가.
    assert resolve_outcome(_FULL_SERIES, created_date=date(2026, 6, 21), trading_days=7) == "skipped"
    assert resolve_outcome([], created_date=date(2026, 6, 22), trading_days=7) == "skipped"


class _FakeBackend:
    """signal_journals 미확정 대상 + upsert 기록 대역."""

    def __init__(self, journals, existing=()):
        self._journals = journals  # [{journal_id, stock_id, created_at}]
        self._existing = set(existing)  # {(journal_id, horizon)}
        self.upserts = []  # (journal_id, horizon, base_date, base, target_date, target, pct)

    async def fetch(self, sql, horizon):
        assert "NOT EXISTS" in sql and "signal_journal_outcomes" in sql
        return [
            j for j in self._journals if (j["journal_id"], horizon) not in self._existing
        ]

    async def execute(self, sql, *args):
        assert "ON CONFLICT (journal_id, horizon) DO UPDATE" in sql
        self.upserts.append(args)
        self._existing.add((args[0], args[1]))
        return "INSERT 0 1"


class _FakeSource:
    def __init__(self, series_by_stock):
        self._series = series_by_stock
        self.fetch_calls = 0

    async def fetch(self, sql, stock_id, start_date):
        assert "FROM ohlcv_data" in sql
        self.fetch_calls += 1
        return [
            {"trade_date": d, "close": c}
            for d, c in self._series.get(stock_id, [])
            if d >= start_date
        ]


def _journal(journal_id, stock_id, created="2026-06-22T10:00:00+09:00"):
    return {
        "journal_id": journal_id,
        "stock_id": stock_id,
        "created_at": datetime.fromisoformat(created),
    }


def test_record_stores_7td_and_leaves_30td_pending():
    backend = _FakeBackend([_journal(20, 10)])
    source = _FakeSource({10: _FULL_SERIES})

    stats = asyncio.run(record_journal_outcomes(backend, source))

    assert stats.stored == 1 and stats.pending == 1 and stats.failed == 0
    (journal_id, horizon, base_date, base, target_date, target, pct) = backend.upserts[0]
    assert (journal_id, horizon) == (20, "7td")
    assert (base_date, target_date) == (date(2026, 6, 22), date(2026, 7, 1))
    assert pct == 1.0


def test_record_is_idempotent_via_not_exists():
    backend = _FakeBackend([_journal(20, 10)], existing=[(20, "7td")])
    source = _FakeSource({10: _FULL_SERIES})

    stats = asyncio.run(record_journal_outcomes(backend, source))

    # 7td 는 이미 확정 → 대상 아님. 30td 만 남고 아직 미도래.
    assert stats.stored == 0 and stats.pending == 1
    assert backend.upserts == []


def test_record_fetches_series_once_per_stock():
    backend = _FakeBackend([_journal(20, 10), _journal(21, 10, "2026-06-24T09:00:00+09:00")])
    source = _FakeSource({10: _FULL_SERIES})

    asyncio.run(record_journal_outcomes(backend, source))

    assert source.fetch_calls == 1


def test_record_kst_conversion_uses_korean_date():
    # UTC 6/22 23:00 = KST 6/23 08:00 → base 는 6/23 이하 최근 거래일(6/23).
    backend = _FakeBackend([_journal(20, 10, "2026-06-22T23:00:00+00:00")])
    source = _FakeSource({10: _FULL_SERIES})

    asyncio.run(record_journal_outcomes(backend, source))

    assert backend.upserts[0][2] == date(2026, 6, 23)


def test_record_isolates_per_journal_failure():
    class _ExplodingBackend(_FakeBackend):
        async def execute(self, sql, *args):
            if args[0] == 20:
                raise RuntimeError("boom")
            return await super().execute(sql, *args)

    backend = _ExplodingBackend([_journal(20, 10), _journal(21, 10)])
    source = _FakeSource({10: _FULL_SERIES})

    stats = asyncio.run(record_journal_outcomes(backend, source))

    assert stats.failed == 1 and stats.stored == 1
    assert any("journal=20" in e for e in stats.errors)


def test_record_no_targets_is_noop():
    backend = _FakeBackend([])
    source = _FakeSource({})

    stats = asyncio.run(record_journal_outcomes(backend, source))

    assert (stats.stored, stats.pending, stats.skipped, stats.failed) == (0, 0, 0, 0)
    assert source.fetch_calls == 0


def test_record_skips_journal_without_price_history():
    backend = _FakeBackend([_journal(20, 99)])
    source = _FakeSource({})  # 시세 없음

    stats = asyncio.run(record_journal_outcomes(backend, source))

    assert stats.skipped >= 1 and stats.stored == 0


class _FakeChartBackend:
    """저널 보유 종목 목록 + 차트 upsert 기록 대역."""

    def __init__(self, stocks):
        self._stocks = stocks  # [{stock_id, first_created}]
        self.upserts = []  # (stock_id, trade_date, close)

    async def fetch(self, sql):
        assert "GROUP BY stock_id" in sql
        return self._stocks

    async def execute(self, sql, *args):
        assert "INSERT INTO signal_journal_chart_prices" in sql
        assert "ON CONFLICT (stock_id, trade_date) DO UPDATE" in sql
        if args[0] == 666:
            raise RuntimeError("boom")
        self.upserts.append(args)
        return "INSERT 0 1"


def test_chart_sync_window_starts_30_days_before_first_journal():
    backend = _FakeChartBackend(
        [{"stock_id": 10, "first_created": datetime.fromisoformat("2026-06-22T10:00:00+09:00")}]
    )
    source = _FakeSource({10: _FULL_SERIES})

    stats = asyncio.run(sync_journal_chart_prices(backend, source))

    # 6/22 - 30일 = 5/23 이후 전 구간(시계열 전체 12행) upsert.
    assert stats.stocks == 1 and stats.rows == len(_FULL_SERIES) and stats.failed == 0
    assert backend.upserts[0][:2] == (10, date(2026, 6, 22))
    # 소스 조회 시작일이 작성일 30일 전인지 — _FakeSource 가 start_date 필터를 적용하므로
    # 5/23 이전 데이터가 있었다면 걸러졌을 것. 여기선 전체 행 수로 간접 확인.


def test_chart_sync_isolates_per_stock_failure():
    backend = _FakeChartBackend(
        [
            {"stock_id": 666, "first_created": datetime.fromisoformat("2026-06-22T10:00:00+09:00")},
            {"stock_id": 10, "first_created": datetime.fromisoformat("2026-06-22T10:00:00+09:00")},
        ]
    )
    source = _FakeSource({666: _FULL_SERIES, 10: _FULL_SERIES})

    stats = asyncio.run(sync_journal_chart_prices(backend, source))

    assert stats.failed == 1 and stats.stocks == 1
    assert any("stock=666" in e for e in stats.errors)
    assert all(u[0] == 10 for u in backend.upserts)


def test_chart_sync_noop_without_journals():
    backend = _FakeChartBackend([])
    source = _FakeSource({})

    stats = asyncio.run(sync_journal_chart_prices(backend, source))

    assert (stats.stocks, stats.rows, stats.failed) == (0, 0, 0)
    assert source.fetch_calls == 0
