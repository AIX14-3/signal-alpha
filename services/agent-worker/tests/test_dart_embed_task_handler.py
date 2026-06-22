import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.analyzers.dart.embedding_features import mean_vector
from app.embeddings.provider import EMBEDDING_DIM, set_embedding_provider
from app.orchestrator.dart.tasks import DartEmbedTaskHandler


class FakeProvider:
    async def embed(self, texts):
        return [[0.5] * EMBEDDING_DIM for _ in texts]


class FakeRawDetailRepo:
    def __init__(self, rows):
        self._rows = rows
        self.queried_ids = None

    async def list_dart_documents_by_raw_ids(self, ids):
        self.queried_ids = ids
        return self._rows


class DartEmbedConn:
    """dart_chunks INSERT, dart_document_features upsert, novelty fetchrow를 관찰/모킹한다."""

    def __init__(self, mean_prior_distance=0.12, prior_chunk_count=5):
        self.inserts = []  # dart_chunks
        self.feature_upserts = []  # dart_document_features
        self._mean_prior_distance = mean_prior_distance
        self._prior_chunk_count = prior_chunk_count

    async def execute(self, sql, *args):
        if "INSERT INTO dart_chunks" in sql:
            self.inserts.append(args)
        elif "INSERT INTO dart_document_features" in sql:
            self.feature_upserts.append(args)

    async def fetchrow(self, sql, *args):
        if "AVG(embedding <=> " in sql:
            return {
                "mean_prior_distance": self._mean_prior_distance,
                "prior_chunk_count": self._prior_chunk_count,
            }
        return None


class DartEmbedTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        set_embedding_provider(FakeProvider())

    def tearDown(self):
        set_embedding_provider(None)

    async def test_embeds_document_text_into_dart_chunks(self):
        conn = DartEmbedConn()
        handler = DartEmbedTaskHandler(conn)
        handler._raw_detail_repository = FakeRawDetailRepo(
            [
                {
                    "raw_document_id": 42,
                    "stock_id": 7,
                    "report_name": "사업보고서",
                    "extra_payload": {"document_text": "공시 본문입니다. " * 250},
                }
            ]
        )

        result = await handler({"task_context": {"raw_document_id": 42}})

        self.assertEqual(result["status"], "embedded")
        self.assertEqual(result["raw_document_id"], 42)
        self.assertEqual(len(conn.inserts), result["chunks"])
        self.assertGreater(result["chunks"], 0)
        # 적재 인자: (raw_document_id, stock_id, chunk_index, chunk_text, token_count, embedding)
        first = conn.inserts[0]
        self.assertEqual(first[0], 42)
        self.assertEqual(first[1], 7)
        # 마지막 인자(embedding)는 1024차원 pgvector 문자열
        emb = first[-1]
        self.assertTrue(isinstance(emb, str) and emb.startswith("["))
        self.assertEqual(emb.count(",") + 1, EMBEDDING_DIM)
        # chunk_index 순차
        self.assertEqual([a[2] for a in conn.inserts], list(range(len(conn.inserts))))
        # 결정론 임베딩 피처(신규성)가 dart_document_features에 적재됨
        self.assertEqual(result["mean_prior_distance"], 0.12)
        self.assertEqual(len(conn.feature_upserts), 1)
        feat = conn.feature_upserts[0]
        self.assertEqual(feat[0], 42)  # raw_document_id
        self.assertEqual(feat[1], 7)  # stock_id
        self.assertEqual(feat[2], 0.12)  # mean_prior_distance
        self.assertEqual(feat[3], 5)  # prior_chunk_count

    async def test_novelty_null_when_no_prior_disclosures(self):
        conn = DartEmbedConn(mean_prior_distance=None, prior_chunk_count=0)
        handler = DartEmbedTaskHandler(conn)
        handler._raw_detail_repository = FakeRawDetailRepo(
            [
                {
                    "raw_document_id": 1,
                    "stock_id": 3,
                    "report_name": "사업보고서",
                    "extra_payload": {"document_text": "최초 공시입니다. " * 250},
                }
            ]
        )
        result = await handler({"task_context": {"raw_document_id": 1}})
        self.assertEqual(result["status"], "embedded")
        self.assertIsNone(result["mean_prior_distance"])
        self.assertEqual(conn.feature_upserts[0][2], None)
        self.assertEqual(conn.feature_upserts[0][3], 0)

    async def test_skips_when_no_raw_document_id(self):
        result = await DartEmbedTaskHandler(DartEmbedConn())({"task_context": {}})
        self.assertEqual(result["status"], "skipped")

    async def test_not_found_when_document_missing(self):
        handler = DartEmbedTaskHandler(DartEmbedConn())
        handler._raw_detail_repository = FakeRawDetailRepo([])
        result = await handler({"task_context": {"raw_document_id": 99}})
        self.assertEqual(result["status"], "not_found")


class MeanVectorTest(unittest.TestCase):
    def test_componentwise_mean(self):
        self.assertEqual(mean_vector([[0.0, 2.0], [2.0, 4.0]]), [1.0, 3.0])

    def test_empty_returns_empty(self):
        self.assertEqual(mean_vector([]), [])

    def test_dimension_mismatch_raises(self):
        with self.assertRaises(ValueError):
            mean_vector([[1.0, 2.0], [3.0]])


if __name__ == "__main__":
    unittest.main()
