import unittest

from signal_alpha_data_access.repositories.ml_inferences import MlInferenceRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [{"id": 1}]

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 1, "model_name": args[3], "pred_value": args[5]}


class MlInferenceRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_inference_is_idempotent_insert(self):
        connection = FakeConnection()
        repository = MlInferenceRepository(connection)

        row = await repository.upsert_inference(
            stock_id=10,
            run_key="ML",
            asof_date="2026-06-22",
            model_name="ewma",
            horizon=10,
            pred_value=0.123,
            device="cpu",
        )

        self.assertEqual(row["model_name"], "ewma")
        self.assertEqual(row["pred_value"], 0.123)
        sql = connection.calls[0][1]
        self.assertIn("INSERT INTO ml_inferences", sql)
        self.assertIn("ON CONFLICT (stock_id, run_key, asof_date, model_name, horizon)", sql)

    async def test_upsert_inference_persists_error_with_null_value(self):
        connection = FakeConnection()
        repository = MlInferenceRepository(connection)

        await repository.upsert_inference(
            stock_id=10,
            run_key="ML",
            asof_date="2026-06-22",
            model_name="garch",
            horizon=10,
            pred_value=None,
            error_message="`arch` not installed",
        )

        args = connection.calls[0][2]
        self.assertIsNone(args[5])  # pred_value
        self.assertEqual(args[8], "`arch` not installed")  # error_message

    async def test_list_for_run_without_horizon_lists_all(self):
        connection = FakeConnection()
        repository = MlInferenceRepository(connection)

        await repository.list_for_run(stock_id=10, run_key="ML", asof_date="2026-06-22")

        sql = connection.calls[0][1]
        self.assertIn("WHERE stock_id = $1 AND run_key = $2 AND asof_date = $3", sql)
        self.assertNotIn("horizon = $4", sql)


if __name__ == "__main__":
    unittest.main()
