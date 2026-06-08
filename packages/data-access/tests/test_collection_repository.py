import unittest

from signal_alpha_data_access.repositories.collection import CollectionRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return 10

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 20, "source_hash": args[5]}

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "OK"

    async def executemany(self, sql, args):
        self.calls.append(("executemany", sql, tuple(args)))
        return "OK"


class CollectionRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_collector_run_uses_running_status_defaults(self):
        connection = FakeConnection()
        repository = CollectionRepository(connection)

        run_id = await repository.create_collector_run("REPORT", "batch")

        self.assertEqual(run_id, 10)
        self.assertEqual(connection.calls[0][2], ("REPORT", "batch"))

    async def test_upsert_raw_document_uses_source_type_external_id_conflict(self):
        connection = FakeConnection()
        repository = CollectionRepository(connection)

        row = await repository.upsert_raw_document(
            stock_id=1,
            collector_run_id=10,
            source_type="REPORT",
            source_name="증권사 리포트",
            external_id="report-1",
            source_hash="abc123",
            title="리포트",
            source_url="https://example.com/report",
            published_at="2026-06-08T00:00:00+09:00",
        )

        self.assertEqual(row["source_hash"], "abc123")
        self.assertIn("ON CONFLICT (source_type, external_id)", connection.calls[0][1])

    async def test_replace_report_chunks_deletes_then_bulk_inserts(self):
        connection = FakeConnection()
        repository = CollectionRepository(connection)

        await repository.replace_report_chunks(
            raw_document_id=20,
            stock_id=1,
            chunks=["첫 번째 chunk", "두 번째 chunk"],
        )

        self.assertEqual(connection.calls[0][0], "execute")
        self.assertIn("DELETE FROM report_chunks", connection.calls[0][1])
        self.assertEqual(connection.calls[1][0], "executemany")
        self.assertEqual(connection.calls[1][2][0], (20, 1, 0, "첫 번째 chunk", None))
