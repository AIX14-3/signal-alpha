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
            {"chunk_text": "목표주가 상향", "raw_document_id": 10, "chunk_index": 0, "similarity": 0.91},
            {"chunk_text": "HBM 호조", "raw_document_id": 10, "chunk_index": 3, "similarity": 0.85},
        ]


class ReportRagRetrieverTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        set_embedding_provider(None)

    async def test_retrieve_filters_by_stock_id_and_embeds_query_once(self):
        provider = FakeProvider()
        conn = FakeConnection()

        rows = await retrieve(conn, stock_id=7, query="목표주가 근거", top_k=2, provider=provider)

        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(rows[0].keys()), ["chunk_index", "chunk_text", "raw_document_id", "similarity"])
        # 질의는 정확히 1회만 임베딩
        self.assertEqual(provider.calls, [["목표주가 근거"]])
        # pgvector 문자열 + stock_id 필터 + top_k 인자
        self.assertTrue(isinstance(conn.last_args[0], str) and conn.last_args[0].startswith("["))
        self.assertEqual(conn.last_args[0].count(",") + 1, EMBEDDING_DIM)
        self.assertEqual(conn.last_args[1], 7)
        self.assertEqual(conn.last_args[2], 2)
        self.assertIn("WHERE stock_id = $2", conn.last_sql)
        self.assertIn("embedding <=> $1::vector", conn.last_sql)

    async def test_callable_retriever_uses_singleton_provider(self):
        set_embedding_provider(FakeProvider())
        conn = FakeConnection()
        retriever = ReportRagRetriever(conn)

        rows = await retriever(1, "리스크 요인", top_k=2)

        self.assertEqual(len(rows), 2)
        self.assertGreaterEqual(rows[0]["similarity"], rows[1]["similarity"])


if __name__ == "__main__":
    unittest.main()
