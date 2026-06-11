import unittest

from signal_alpha_data_access.repositories.report_chunks import ReportChunkRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [{"id": 1, "chunk_text": "리포트 본문"}]

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "OK"


class ReportChunkRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_list_embedding_pending_filters_null_embeddings(self):
        connection = FakeConnection()
        repository = ReportChunkRepository(connection)

        rows = await repository.list_embedding_pending(limit=10)

        self.assertEqual(rows[0]["id"], 1)
        self.assertIn("embedding IS NULL", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], (10,))

    async def test_update_embedding_casts_vector_parameter(self):
        connection = FakeConnection()
        repository = ReportChunkRepository(connection)

        await repository.update_embedding(chunk_id=1, embedding=[0.1, 0.2])

        self.assertIn("$2::vector", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], (1, "[0.1,0.2]"))

    async def test_search_similar_uses_pgvector_distance_order(self):
        connection = FakeConnection()
        repository = ReportChunkRepository(connection)

        await repository.search_similar(stock_id=1, query_embedding=[0.1, 0.2], limit=5)

        self.assertIn("embedding <=> $2::vector AS distance", connection.calls[0][1])
        self.assertIn("ORDER BY embedding <=> $2::vector", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], (1, "[0.1,0.2]", 5))
