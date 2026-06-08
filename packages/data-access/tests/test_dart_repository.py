import unittest

from signal_alpha_data_access.repositories.dart import DartRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"corp_code": args[0] if args else "00126380"}

    async def executemany(self, sql, args):
        self.calls.append(("executemany", sql, args))


class DartRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_corp_code_uses_corp_code_conflict(self):
        connection = FakeConnection()
        repository = DartRepository(connection)

        await repository.upsert_corp_code(
            corp_code="00126380",
            ticker="005930",
            corp_name="삼성전자",
            stock_id=1,
        )

        self.assertIn("ON CONFLICT (corp_code)", connection.calls[0][1])

    async def test_get_corp_code_by_ticker_normalizes_ticker(self):
        connection = FakeConnection()
        repository = DartRepository(connection)

        await repository.get_corp_code_by_ticker(" 005930 ")

        self.assertEqual(connection.calls[0][2], ("005930",))

    async def test_upsert_listed_corp_codes_links_stock_by_ticker(self):
        connection = FakeConnection()
        repository = DartRepository(connection)

        count = await repository.upsert_listed_corp_codes(
            [
                {
                    "corp_code": "00126380",
                    "ticker": " 005930 ",
                    "corp_name": "Samsung Electronics",
                    "stock_name": "Samsung Electronics",
                }
            ]
        )

        self.assertEqual(count, 1)
        self.assertIn("SELECT id FROM stocks WHERE ticker = $2", connection.calls[0][1])
        self.assertIn("ON CONFLICT (ticker)", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2][0][0:3], ("00126380", "005930", "Samsung Electronics"))
