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
        return 10

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "OK"


class UserSignalRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_add_watchlist_uses_user_stock_conflict(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        await repository.add_watchlist(user_id=1, stock_id=10)

        self.assertIn("ON CONFLICT (user_id, stock_id)", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2][2], False)

    async def test_list_watchlist_joins_stocks(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        await repository.list_watchlist(user_id=1)

        self.assertIn("INNER JOIN stocks", connection.calls[0][1])

    async def test_count_watchlist_counts_user_rows(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        count = await repository.count_watchlist(user_id=1)

        self.assertEqual(count, 10)
        self.assertIn("COUNT(*)", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], (1,))

    async def test_get_watchlist_item_joins_stock_by_user_and_stock(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        await repository.get_watchlist_item(user_id=1, stock_id=10)

        self.assertIn("INNER JOIN stocks", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], (1, 10))

    async def test_mark_signal_read_uses_read_unique_key(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        await repository.mark_signal_read(user_id=1, final_signal_id=20)

        self.assertIn("ON CONFLICT (user_id, final_signal_id)", connection.calls[0][1])

    async def test_create_journal_returns_inserted_id(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        journal_id = await repository.create_journal(user_id=1, stock_id=10, user_view="watch")

        self.assertEqual(journal_id, 10)
        self.assertIn("INSERT INTO signal_journals", connection.calls[0][1])

    async def test_create_journal_entry_returns_inserted_row_with_tags(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        row = await repository.create_journal_entry(
            user_id=1,
            stock_id=10,
            final_signal_id=20,
            user_view="research_more",
            user_memo="추가 근거 확인이 필요합니다.",
            tags=["DART"],
            signal_score_at_time=50,
            signal_value_at_time="neutral",
            source_agreement_at_time="LOW",
        )

        self.assertEqual(row["id"], 1)
        sql = connection.calls[0][1]
        self.assertIn("INSERT INTO signal_journals", sql)
        self.assertIn("tags", sql)
        self.assertIn("RETURNING", sql)
        self.assertEqual(connection.calls[0][2][3], "research_more")

    async def test_list_journals_filters_user_and_optional_stock_code(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        await repository.list_journals(user_id=1, stock_code="005930", limit=20)

        sql = connection.calls[0][1]
        self.assertIn("FROM signal_journals", sql)
        self.assertIn("INNER JOIN stocks", sql)
        self.assertIn("stocks.ticker = $2", sql)
        self.assertEqual(connection.calls[0][2], (1, "005930", 20))

    async def test_get_journal_filters_by_user(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        await repository.get_journal(user_id=1, journal_id=20)

        sql = connection.calls[0][1]
        self.assertIn("signal_journals.id = $1", sql)
        self.assertIn("signal_journals.user_id = $2", sql)
        self.assertEqual(connection.calls[0][2], (20, 1))

    async def test_update_journal_updates_user_view_memo_and_tags(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        await repository.update_journal(
            user_id=1,
            journal_id=20,
            user_view="not_relevant",
            user_memo="낮은 관련도",
            tags=["검토완료"],
        )

        sql = connection.calls[0][1]
        self.assertIn("UPDATE signal_journals", sql)
        self.assertIn("tags = $5::JSONB", sql)
        self.assertIn("updated_at = NOW()", sql)
        self.assertEqual(connection.calls[0][2][0:2], (20, 1))

    async def test_delete_journal_deletes_only_user_row(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        await repository.delete_journal(user_id=1, journal_id=20)

        sql = connection.calls[0][1]
        self.assertIn("DELETE FROM signal_journals", sql)
        self.assertIn("user_id = $2", sql)
        self.assertEqual(connection.calls[0][2], (20, 1))

    async def test_list_latest_journals_by_stock_ids_groups_user_journals(self):
        connection = FakeConnection()
        repository = UserSignalRepository(connection)

        await repository.list_latest_journals_by_stock_ids(user_id=1, stock_ids=[10, 11])

        sql = connection.calls[0][1]
        self.assertIn("DISTINCT ON (stock_id)", sql)
        self.assertIn("stock_id = ANY", sql)
        self.assertEqual(connection.calls[0][2], (1, [10, 11]))
