import unittest

from signal_alpha_data_access.repositories.collection import CollectionRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "DELETE FROM raw_documents" in sql:
            return 2
        if "DELETE FROM agent_results" in sql or "DELETE FROM analysis_results" in sql:
            return 1
        return 0


class DartCleanupRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_delete_dart_test_data_removes_analysis_before_raw_documents(self):
        connection = FakeConnection()
        repository = CollectionRepository(connection)

        row = await repository.delete_dart_test_data(
            stock_code="005930",
            bgn_de="2026-06-01",
            end_de="2026-06-30",
        )

        self.assertEqual(row["deleted_raw_document_count"], 2)
        sql_calls = [call[1] for call in connection.calls]
        self.assertIn("run_key LIKE 'DART%'", sql_calls[0])
        self.assertIn("DELETE FROM agent_results", sql_calls[2])
        self.assertIn("DELETE FROM analysis_results", sql_calls[3])
        self.assertIn("DELETE FROM signal_events", sql_calls[4])
        self.assertIn("DELETE FROM raw_documents", sql_calls[5])
        self.assertEqual(connection.calls[0][2], ("005930", "2026-06-01", "2026-06-30"))


if __name__ == "__main__":
    unittest.main()
