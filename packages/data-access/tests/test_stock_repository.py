import unittest

from signal_alpha_data_access.repositories.stocks import StockRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 1, "ticker": args[0], "name": "삼성전자", "market": "KOSPI"}

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [{"id": 1, "ticker": "005930"}]


class StockRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_by_ticker_normalizes_ticker(self):
        connection = FakeConnection()
        repository = StockRepository(connection)

        row = await repository.get_by_ticker(" 005930 ")

        self.assertEqual(row["ticker"], "005930")
        self.assertEqual(connection.calls[0][2], ("005930",))

    async def test_list_active_limits_rows(self):
        connection = FakeConnection()
        repository = StockRepository(connection)

        rows = await repository.list_active(limit=5)

        self.assertEqual(rows, [{"id": 1, "ticker": "005930"}])
        self.assertEqual(connection.calls[0][2], (5,))

    async def test_search_active_matches_ticker_or_name(self):
        connection = FakeConnection()
        repository = StockRepository(connection)

        rows = await repository.search_active("삼성", limit=10)

        self.assertEqual(rows, [{"id": 1, "ticker": "005930"}])
        sql = connection.calls[0][1]
        self.assertIn("ticker ILIKE", sql)
        self.assertIn("name ILIKE", sql)
        self.assertEqual(connection.calls[0][2], ("%삼성%", 10))

    async def test_ensure_stock_upserts_by_ticker(self):
        connection = FakeConnection()
        repository = StockRepository(connection)

        row = await repository.ensure_stock(
            ticker="000660",
            name="SK하이닉스",
            market="KOSPI",
            sector="반도체",
        )

        self.assertEqual(row["ticker"], "000660")
        self.assertIn("ON CONFLICT (ticker)", connection.calls[0][1])
