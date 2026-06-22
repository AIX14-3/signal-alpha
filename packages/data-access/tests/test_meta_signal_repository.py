import json
import unittest

from signal_alpha_data_access.repositories.meta_signals import MetaSignalRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if len(args) >= 9:  # upsert_meta_signal
            return {"id": 1, "method": args[6], "combined_vol": args[4]}
        return {"id": 1}  # get (natural-key lookup)


class MetaSignalRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_meta_signal_idempotent_insert(self):
        connection = FakeConnection()
        repository = MetaSignalRepository(connection)

        row = await repository.upsert_meta_signal(
            stock_id=10,
            run_key="ML",
            asof_date="2026-06-22",
            horizon=10,
            combined_vol=0.31,
            confidence=0.8,
            method="stacking",
            model_count=4,
            weight_breakdown={"ewma": 0.25, "har_rv": 0.75},
        )

        self.assertEqual(row["method"], "stacking")
        self.assertEqual(row["combined_vol"], 0.31)
        sql = connection.calls[0][1]
        self.assertIn("INSERT INTO meta_signals", sql)
        self.assertIn("ON CONFLICT (stock_id, run_key, asof_date, horizon)", sql)
        # weight_breakdown is serialized to JSON text for the JSONB column
        self.assertEqual(json.loads(connection.calls[0][2][8]), {"ewma": 0.25, "har_rv": 0.75})

    async def test_upsert_meta_signal_handles_null_combined(self):
        connection = FakeConnection()
        repository = MetaSignalRepository(connection)

        await repository.upsert_meta_signal(
            stock_id=10,
            run_key="ML",
            asof_date="2026-06-22",
            horizon=10,
            combined_vol=None,
            confidence=0.0,
            method="empty",
            model_count=0,
        )

        args = connection.calls[0][2]
        self.assertIsNone(args[4])  # combined_vol
        self.assertEqual(json.loads(args[8]), {})  # weight_breakdown defaults to {}

    async def test_get_filters_by_natural_key(self):
        connection = FakeConnection()
        repository = MetaSignalRepository(connection)

        await repository.get(stock_id=10, run_key="ML", asof_date="2026-06-22", horizon=10)

        sql = connection.calls[0][1]
        self.assertIn("WHERE stock_id = $1 AND run_key = $2 AND asof_date = $3 AND horizon = $4", sql)


if __name__ == "__main__":
    unittest.main()
