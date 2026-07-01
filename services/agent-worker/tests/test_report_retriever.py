import unittest
from datetime import date

from app.agents.report.retriever import retrieve


class FakeEmbedder:
    async def embed(self, text):
        return [0.1] * 768


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class RetrieverConn:
    def __init__(self):
        self.fetched = []
        self.executed = []

    def transaction(self):
        return FakeTransaction()

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "SET"

    async def fetch(self, sql, *args):
        self.fetched.append((sql, args))
        return [
            {
                "chunk_text": "HBM 수요 견조",
                "raw_document_id": 42,
                "broker": "신한투자증권",
                "publish_date": date(2026, 6, 24),
            }
        ]


class ReportRetrieverTest(unittest.IsolatedAsyncioTestCase):
    async def test_cosine_topk_scoped_by_stock_with_vector_literal(self):
        conn = RetrieverConn()

        rows = await retrieve(conn, stock_id=7, query="리스크 요인은?", embedder=FakeEmbedder(), top_k=5)

        sql, args = conn.fetched[0]
        self.assertIn("embedding <=> $1::vector", sql)
        self.assertIn("WHERE rd.stock_id = $2", sql)
        self.assertIn("LIMIT $3", sql)
        # $1 is the embedded query, bound as a pgvector literal string.
        self.assertTrue(str(args[0]).startswith("["))
        self.assertEqual(args[1], 7)
        self.assertEqual(args[2], 5)
        # provenance returned for grounding/citation.
        self.assertEqual(rows[0]["broker"], "신한투자증권")
        self.assertEqual(rows[0]["raw_document_id"], 42)
        # ef_search raised (transaction-local) to protect stock-filtered recall.
        self.assertTrue(any("hnsw.ef_search" in sql for sql, _ in conn.executed))


if __name__ == "__main__":
    unittest.main()
