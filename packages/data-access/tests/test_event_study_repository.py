import unittest

from signal_alpha_data_access.repositories.event_study import EventStudyRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [{"id": 1}]

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 1, "signal_event_id": args[0]}


class EventStudyRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_panel_row_uses_on_conflict(self):
        connection = FakeConnection()
        repository = EventStudyRepository(connection)

        row = await repository.upsert_panel_row(
            signal_event_id=7,
            stock_id=10,
            asof_date="2026-01-06",
            fwd_return_1d=0.05,
            fwd_return_5d=None,
            fwd_return_20d=None,
            abnormal_return_20d=None,
        )

        self.assertEqual(row["signal_event_id"], 7)
        sql = connection.calls[0][1]
        self.assertIn("INSERT INTO event_study_panel", sql)
        self.assertIn("ON CONFLICT (signal_event_id, asof_date)", sql)
        # 기본 universe_snapshot 이 인자로 전달된다.
        self.assertEqual(connection.calls[0][2][7], "kospi20_seed")

    async def test_list_for_training_filters_date_range(self):
        connection = FakeConnection()
        repository = EventStudyRepository(connection)

        await repository.list_for_training(
            asof_from="2026-01-01", asof_to="2026-03-31", universe_snapshot="kospi20_seed"
        )

        sql = connection.calls[0][1]
        self.assertIn("asof_date BETWEEN $1 AND $2", sql)
        self.assertEqual(
            connection.calls[0][2], ("2026-01-01", "2026-03-31", "kospi20_seed")
        )

    async def test_list_for_training_universe_optional(self):
        connection = FakeConnection()
        repository = EventStudyRepository(connection)

        await repository.list_for_training(asof_from="2026-01-01", asof_to="2026-03-31")

        self.assertEqual(connection.calls[0][2], ("2026-01-01", "2026-03-31", None))


if __name__ == "__main__":
    unittest.main()
