import unittest
from datetime import date

from app.agents.report import retriever as retriever_mod
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
    def __init__(self, *, pgvector_version="0.8.2"):
        self.fetched = []
        self.executed = []
        self._pgvector_version = pgvector_version

    def transaction(self):
        return FakeTransaction()

    async def fetchval(self, sql, *args):
        if "pg_extension" in sql:
            return self._pgvector_version
        return None

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
    def setUp(self):
        retriever_mod._ITERATIVE_SCAN = None  # 버전 감지 캐시 초기화(테스트 순서 무관)

    def tearDown(self):
        retriever_mod._ITERATIVE_SCAN = None

    async def test_cosine_topk_scoped_by_stock_with_vector_literal(self):
        conn = RetrieverConn()

        rows = await retrieve(conn, stock_id=7, query="리스크 요인은?", embedder=FakeEmbedder(), top_k=5)

        sql, args = conn.fetched[0]
        self.assertIn("embedding <=> $1::vector", sql)
        self.assertIn("WHERE rc.stock_id = $2", sql)  # 1B: report_chunks.stock_id 직접 필터
        self.assertNotIn("JOIN raw_documents", sql)   # raw_documents 조인 제거(exact 스캔)
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
        # pgvector 0.8.2 → iterative_scan 도 켜진다(버전-적응형 1A).
        self.assertTrue(any("hnsw.iterative_scan" in sql for sql, _ in conn.executed))

    async def test_iterative_scan_skipped_on_old_pgvector(self):
        # 0.7.x → GUC 미존재라 iterative_scan 은 안 켜고 ef_search 만(구버전 안전).
        conn = RetrieverConn(pgvector_version="0.7.4")

        await retrieve(conn, stock_id=7, query="q", embedder=FakeEmbedder(), top_k=5)

        self.assertTrue(any("hnsw.ef_search" in sql for sql, _ in conn.executed))
        self.assertFalse(any("hnsw.iterative_scan" in sql for sql, _ in conn.executed))


if __name__ == "__main__":
    unittest.main()
