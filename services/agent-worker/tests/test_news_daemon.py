import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.clients.naver_news_client import NaverNewsError, NaverNewsItem
from app.news.daemon import _select_due, run_news_cycle

_NOW = datetime(2026, 7, 7, 0, 0, tzinfo=UTC)


def _settings(**overrides):
    values = {
        "news_refresh_hours": 6.0,
        "news_batch_size": 20,
        "news_lookback_days": 14,
        "news_max_items": 20,
        "news_fetch_timeout_seconds": 15,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _rfc2822(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


class FakeNewsClient:
    def __init__(self, *, items=None, raise_for=()):
        self._items = items if items is not None else [
            NaverNewsItem("속보", "요약", "https://news.example/1", _rfc2822(_NOW - timedelta(hours=1)))
        ]
        self._raise_for = set(raise_for)
        self.queries = []

    async def search(self, query, *, display=20, sort="date"):
        self.queries.append(query)
        if query in self._raise_for:
            raise NaverNewsError("quota exceeded")
        return list(self._items)


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class FakeConnection:
    def __init__(self, stocks, last_collected):
        self.stocks = stocks
        self.last_collected = last_collected
        self.inserted = []
        self._next_id = 1

    def transaction(self):
        return _FakeTx()

    async def fetch(self, sql, *args):
        if "FROM stocks" in sql:
            return self.stocks
        if "MAX(collected_at)" in sql:
            return self.last_collected
        raise AssertionError(f"Unexpected fetch SQL: {sql}")

    async def fetchval(self, sql, *args):
        if "INSERT INTO stock_news" in sql:
            self.inserted.append(args)
            rid = self._next_id
            self._next_id += 1
            return rid
        raise AssertionError(f"Unexpected fetchval SQL: {sql}")


class FakePool:
    def __init__(self, connection):
        self._connection = connection

    def acquire(self):
        return _FakeAcquire(self._connection)


class SelectDueTest(unittest.TestCase):
    def test_uncollected_first_then_stale_oldest(self):
        stocks = [{"id": 1, "ticker": "A", "name": "가"}, {"id": 2, "ticker": "B", "name": "나"}, {"id": 3, "ticker": "C", "name": "다"}]
        last = {2: _NOW - timedelta(hours=10), 3: _NOW - timedelta(hours=1)}  # 3 은 최근(제외)
        due = _select_due(stocks, last, refresh_hours=6, batch_size=10, now=_NOW)
        ids = [s["id"] for s in due]
        self.assertEqual(ids, [1, 2])  # 미수집(1) 먼저, 그다음 오래된 2. 최근 3 제외.

    def test_batch_size_caps(self):
        stocks = [{"id": i, "ticker": str(i), "name": str(i)} for i in range(5)]
        due = _select_due(stocks, {}, refresh_hours=6, batch_size=2, now=_NOW)
        self.assertEqual(len(due), 2)


class RunNewsCycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_inserts_for_due_stocks(self):
        conn = FakeConnection(
            stocks=[{"id": 1, "ticker": "000660", "name": "SK하이닉스"}, {"id": 2, "ticker": "005930", "name": "삼성전자"}],
            last_collected=[],
        )
        client = FakeNewsClient()
        summary = await run_news_cycle(FakePool(conn), _settings(), client=client, now=_NOW)
        self.assertEqual(summary["due"], 2)
        self.assertEqual(summary["processed"], 2)
        self.assertEqual(summary["inserted"], 2)
        self.assertEqual(len(conn.inserted), 2)

    async def test_per_stock_fetch_failure_is_skipped(self):
        conn = FakeConnection(
            stocks=[{"id": 1, "ticker": "000660", "name": "SK하이닉스"}, {"id": 2, "ticker": "005930", "name": "삼성전자"}],
            last_collected=[],
        )
        client = FakeNewsClient(raise_for={"SK하이닉스"})
        summary = await run_news_cycle(FakePool(conn), _settings(), client=client, now=_NOW)
        # 하이닉스는 실패로 스킵, 삼성만 처리·적재.
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["inserted"], 1)
        self.assertEqual(len(conn.inserted), 1)

    async def test_no_active_stocks(self):
        conn = FakeConnection(stocks=[], last_collected=[])
        summary = await run_news_cycle(FakePool(conn), _settings(), client=FakeNewsClient(), now=_NOW)
        self.assertEqual(summary, {"due": 0, "processed": 0, "inserted": 0})


if __name__ == "__main__":
    unittest.main()
