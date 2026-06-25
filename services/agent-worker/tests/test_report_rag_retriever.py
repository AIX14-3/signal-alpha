import unittest

from app.analyzers.report.rag_retriever import ReportRagRetriever, retrieve
from app.embeddings.provider import EMBEDDING_DIM, set_embedding_provider


class FakeProvider:
    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        return [[0.25] * EMBEDDING_DIM for _ in texts]


class FakeConnection:
    def __init__(self):
        self.last_sql = None
        self.last_args = None

    async def fetch(self, sql, *args):
        self.last_sql = sql
        self.last_args = args
        return [
            {
                "chunk_text": "target price raised",
                "raw_document_id": 10,
                "chunk_index": 0,
                "similarity": 0.91,
                "title": "Samsung report",
                "source_url": "https://example.com/report.pdf",
                "securities_firm": "Test Securities",
                "publish_date": "2026-06-24",
            },
            {
                "chunk_text": "HBM demand remains firm",
                "raw_document_id": 10,
                "chunk_index": 3,
                "similarity": 0.85,
                "title": "Samsung report",
                "source_url": "https://example.com/report.pdf",
                "securities_firm": "Test Securities",
                "publish_date": "2026-06-24",
            },
        ]


class ReportRagRetrieverTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        set_embedding_provider(None)

    async def test_retrieve_filters_by_stock_id_and_includes_evidence_metadata(self):
        provider = FakeProvider()
        conn = FakeConnection()

        rows = await retrieve(conn, stock_id=7, query="target evidence", top_k=2, provider=provider)

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            sorted(rows[0].keys()),
            [
                "chunk_index",
                "chunk_text",
                "publish_date",
                "raw_document_id",
                "securities_firm",
                "similarity",
                "source_url",
                "title",
            ],
        )
        self.assertEqual(provider.calls, [["target evidence"]])
        self.assertTrue(isinstance(conn.last_args[0], str) and conn.last_args[0].startswith("["))
        self.assertEqual(conn.last_args[0].count(",") + 1, EMBEDDING_DIM)
        self.assertEqual(conn.last_args[1], 7)
        self.assertEqual(conn.last_args[2], 2)
        self.assertIn("WHERE rc.stock_id = $2", conn.last_sql)
        self.assertIn("JOIN raw_documents", conn.last_sql)
        self.assertIn("JOIN report_raw_details", conn.last_sql)
        self.assertIn("embedding <=> $1::vector", conn.last_sql)

    async def test_callable_retriever_uses_singleton_provider(self):
        set_embedding_provider(FakeProvider())
        conn = FakeConnection()
        retriever = ReportRagRetriever(conn)

        rows = await retriever(1, "risk factors", top_k=2)

        self.assertEqual(len(rows), 2)
        self.assertGreaterEqual(rows[0]["similarity"], rows[1]["similarity"])


if __name__ == "__main__":
    unittest.main()
