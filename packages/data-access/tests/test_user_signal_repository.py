import unittest

from signal_alpha_data_access.repositories.user_signals import UserSignalRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 1, "user_id": args[0]}

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [{"id": 1}]

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return 1

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "OK"


class UserSignalRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_add_watchlist_uses_user_stock_conflict(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        await repository.add_watchlist(user_id=1, stock_id=10)

        self.assertIn("ON CONFLICT (user_id, stock_id)", connection.calls[0][1])

    async def test_list_watchlist_joins_stocks(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        await repository.list_watchlist(user_id=1)

        self.assertIn("INNER JOIN stocks", connection.calls[0][1])

    async def test_mark_signal_read_uses_read_unique_key(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        await repository.mark_signal_read(user_id=1, final_signal_id=20)

        self.assertIn("ON CONFLICT (user_id, final_signal_id)", connection.calls[0][1])

    async def test_create_journal_returns_inserted_id(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        journal_id = await repository.create_journal(user_id=1, stock_id=10, user_view="watch")

        self.assertEqual(journal_id, 1)
        self.assertIn("INSERT INTO signal_journals", connection.calls[0][1])
