import unittest

from signal_alpha_data_access.repositories.analysis import AnalysisRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": args[0], "is_published": False, "needs_review": True, "warning_level": "WARNING"}


class AnalysisRepositoryVetoTest(unittest.IsolatedAsyncioTestCase):
    async def test_apply_risk_veto_unpublishes_signal(self):
        connection = FakeConnection()
        repository = AnalysisRepository(connection)

        row = await repository.apply_risk_veto(final_signal_id=55)

        self.assertEqual(row["id"], 55)
        self.assertFalse(row["is_published"])
        sql = connection.calls[0][1]
        self.assertIn("UPDATE final_signals", sql)
        self.assertIn("is_published = FALSE", sql)
        self.assertIn("needs_review = TRUE", sql)
        self.assertIn("warning_level = 'WARNING'", sql)
        self.assertIn("published_at = NULL", sql)
        self.assertEqual(connection.calls[0][2], (55,))


if __name__ == "__main__":
    unittest.main()
