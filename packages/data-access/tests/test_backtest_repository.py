import unittest

from signal_alpha_data_access.repositories.backtests import BacktestRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return 1

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [{"id": 1}]

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": args[0], "is_hit": args[3]}


class BacktestRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_backtest_result_inserts_result(self):
        connection = FakeConnection()
        repository = BacktestRepository(connection)

        result_id = await repository.create_result(
            final_signal_id=1,
            stock_id=10,
            signal_date="2026-06-08",
            signal_score=60,
            signal_value="neutral",
            price_at_signal=1000,
        )

        self.assertEqual(result_id, 1)
        self.assertIn("INSERT INTO backtest_results", connection.calls[0][1])

    async def test_list_pending_checks_filters_unchecked_rows(self):
        connection = FakeConnection()
        repository = BacktestRepository(connection)

        await repository.list_pending_checks(limit=20)

        self.assertIn("checked_at IS NULL", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], (20,))

    async def test_complete_result_updates_outcome_fields(self):
        connection = FakeConnection()
        repository = BacktestRepository(connection)

        row = await repository.complete_result(
            result_id=1,
            price_after_5d=1100,
            change_pct_5d=10,
            is_hit=True,
        )

        self.assertTrue(row["is_hit"])
        self.assertIn("checked_at = NOW()", connection.calls[0][1])

    async def test_get_signal_posthoc_alignment_summary_aggregates_backtest_results(self):
        connection = FakeConnection()
        repository = BacktestRepository(connection)

        await repository.get_signal_posthoc_alignment_summary()

        sql = connection.calls[0][1]
        self.assertIn("FROM backtest_results", sql)
        self.assertIn("'5td'", sql)
        self.assertIn("is_hit IS NOT NULL", sql)
        self.assertIn("checked_at IS NULL", sql)
        self.assertIn("aligned_count", sql)
        self.assertIn("pending_count", sql)
