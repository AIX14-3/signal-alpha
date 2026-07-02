import unittest

from signal_alpha_data_access.repositories.market_data import MarketDataRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 1, "stock_id": args[0]}

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [{"trade_date": "2026-06-08", "close": 1000}]


class MarketDataRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_ohlcv_uses_stock_date_conflict(self):
        connection = FakeConnection()
        repository = MarketDataRepository(connection)

        await repository.upsert_ohlcv(
            stock_id=1,
            trade_date="2026-06-08",
            open_price=100,
            high_price=110,
            low_price=90,
            close_price=105,
            volume=1000,
        )

        self.assertIn("ON CONFLICT (stock_id, trade_date)", connection.calls[0][1])

    async def test_list_ohlcv_between_orders_by_trade_date(self):
        connection = FakeConnection()
        repository = MarketDataRepository(connection)

        rows = await repository.list_ohlcv_between(
            stock_id=1,
            start_date="2026-06-01",
            end_date="2026-06-08",
        )

        self.assertEqual(rows[0]["close"], 1000)
        self.assertIn("ORDER BY trade_date ASC", connection.calls[0][1])

    async def test_list_recent_ohlcv_limits_newest_then_orders_ascending(self):
        connection = FakeConnection()
        repository = MarketDataRepository(connection)

        await repository.list_recent_ohlcv(stock_id=1, limit=120)

        sql = connection.calls[0][1]
        self.assertIn("ORDER BY trade_date DESC", sql)
        self.assertIn("LIMIT $2", sql)
        self.assertIn("ORDER BY trade_date ASC", sql)
        self.assertEqual(connection.calls[0][2], (1, 120))

    async def test_list_sessions_from_orders_ascending_with_limit(self):
        connection = FakeConnection()
        repository = MarketDataRepository(connection)

        rows = await repository.list_sessions_from(
            stock_id=1, from_date="2026-06-01", limit=61
        )

        sql = connection.calls[0][1]
        self.assertIn("trade_date >= $2", sql)
        self.assertIn("ORDER BY trade_date ASC", sql)
        self.assertIn("LIMIT $3", sql)
        self.assertEqual(connection.calls[0][2], (1, "2026-06-01", 61))
        self.assertEqual(rows[0]["trade_date"], "2026-06-08")

    async def test_upsert_fundamental_uses_unique_period(self):
        connection = FakeConnection()
        repository = MarketDataRepository(connection)

        await repository.upsert_fundamental(
            stock_id=1,
            fiscal_date="2026-03-31",
            period_type="quarter",
        )

        self.assertIn("ON CONFLICT (stock_id, fiscal_date, period_type)", connection.calls[0][1])
