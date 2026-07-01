import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.clients.embedding_client import EmbeddingError
from app.orchestrator.report import tasks as report_tasks
from app.orchestrator.report.tasks import ReportEmbedTaskHandler


class FakeStorage:
    def __init__(self, pdf_bytes=b"%PDF-fake"):
        self.pdf_bytes = pdf_bytes
        self.downloaded_keys = []

    def download_pdf(self, key):
        self.downloaded_keys.append(key)
        return self.pdf_bytes


class FakeEmbedder:
    model = "fake-embed"

    def __init__(self, dim=768):
        self.dim = dim
        self.batches = []

    async def embed_batch(self, texts):
        self.batches.append(list(texts))
        return [[0.1] * self.dim for _ in texts]


class RaisingEmbedder:
    model = "fake-embed"

    async def embed_batch(self, texts):
        raise EmbeddingError("boom")


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class EmbedHandlerConn:
    def __init__(self, *, already=False, parsing_status="success", s3_key="reports/x.pdf", extracted_text="preview"):
        self.executed = []
        self._already = already
        self._parsing_status = parsing_status
        self._s3_key = s3_key
        self._extracted_text = extracted_text

    def transaction(self):
        return FakeTransaction()

    async def fetchrow(self, sql, *args):
        if "FROM report_raw_details" in sql:
            return {
                "raw_document_id": args[0],
                "s3_key": self._s3_key,
                "extracted_text": self._extracted_text,
                "parsing_status": self._parsing_status,
                "stock_id": 1,
            }
        return None

    async def fetchval(self, sql, *args):
        if "SELECT 1 FROM report_chunks" in sql:
            return 1 if self._already else None
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        # 실제 asyncpg 처럼 INSERT 태그를 반환 → 핸들러의 실제-적재-카운트(_rows_affected) 검증.
        return "INSERT 0 1" if "INSERT INTO report_chunks" in sql else "OK"

    def _chunk_inserts(self):
        return [(sql, args) for sql, args in self.executed if "INSERT INTO report_chunks" in sql]


class ReportEmbedTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_chunk = report_tasks._chunk_report_text
        self._orig_extract = report_tasks._extract_pdf_text

    def tearDown(self):
        report_tasks._chunk_report_text = self._orig_chunk
        report_tasks._extract_pdf_text = self._orig_extract

    async def test_reextracts_pdf_and_embeds_chunks(self):
        report_tasks._chunk_report_text = lambda text: ["chunk one", "chunk two"]
        report_tasks._extract_pdf_text = lambda pdf_bytes: "full report text"
        conn = EmbedHandlerConn()
        storage = FakeStorage()
        embedder = FakeEmbedder()
        handler = ReportEmbedTaskHandler(
            connection=conn, settings=object(), storage=storage, embedder=embedder
        )

        result = await handler({"task_context": {"raw_document_id": 42}})

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["chunks"], 2)  # 실제 적재 행수(태그 'INSERT 0 1' 파싱)
        self.assertEqual(result["attempted"], 2)  # 시도 청크 수
        # option 2: full PDF re-extracted from storage, not the 2000-char preview.
        self.assertEqual(storage.downloaded_keys, ["reports/x.pdf"])
        self.assertEqual(embedder.batches, [["chunk one", "chunk two"]])
        inserts = conn._chunk_inserts()
        self.assertEqual(len(inserts), 2)
        # $4 bound as a pgvector literal string.
        self.assertTrue(str(inserts[0][1][3]).startswith("["))
        self.assertEqual(inserts[0][1][1], 0)  # chunk_index
        self.assertEqual(inserts[1][1][1], 1)

    async def test_falls_back_to_preview_when_pdf_missing(self):
        report_tasks._chunk_report_text = lambda text: [text]
        report_tasks._extract_pdf_text = lambda pdf_bytes: "unused"
        conn = EmbedHandlerConn(s3_key=None, extracted_text="preview text")
        embedder = FakeEmbedder()
        handler = ReportEmbedTaskHandler(connection=conn, settings=object(), embedder=embedder)

        result = await handler({"task_context": {"raw_document_id": 42}})

        self.assertEqual(result["status"], "success")
        self.assertEqual(embedder.batches, [["preview text"]])

    async def test_already_embedded_short_circuits(self):
        conn = EmbedHandlerConn(already=True)
        handler = ReportEmbedTaskHandler(connection=conn, settings=object(), embedder=FakeEmbedder())

        result = await handler({"task_context": {"raw_document_id": 42}})

        self.assertEqual(result["status"], "already_embedded")
        self.assertEqual(conn._chunk_inserts(), [])

    async def test_embed_error_is_swallowed(self):
        report_tasks._chunk_report_text = lambda text: ["a chunk"]
        report_tasks._extract_pdf_text = lambda pdf_bytes: "full text"
        conn = EmbedHandlerConn()
        handler = ReportEmbedTaskHandler(
            connection=conn, settings=object(), storage=FakeStorage(), embedder=RaisingEmbedder()
        )

        result = await handler({"task_context": {"raw_document_id": 42}})

        self.assertEqual(result["status"], "embed_error")
        self.assertEqual(conn._chunk_inserts(), [])

    async def test_no_text_when_nothing_to_embed(self):
        conn = EmbedHandlerConn(s3_key=None, extracted_text="")
        handler = ReportEmbedTaskHandler(connection=conn, settings=object(), embedder=FakeEmbedder())

        result = await handler({"task_context": {"raw_document_id": 42}})

        self.assertEqual(result["status"], "no_text")
        self.assertEqual(conn._chunk_inserts(), [])

    async def test_chunks_counts_actual_inserts_not_attempts(self):
        # ON CONFLICT 로 일부가 스킵('INSERT 0 0')되면 chunks 는 실제 적재분만, attempted 는 시도수.
        report_tasks._chunk_report_text = lambda text: ["a", "b", "c"]
        report_tasks._extract_pdf_text = lambda pdf_bytes: "full text"

        class _SkipSecondConn(EmbedHandlerConn):
            async def execute(self, sql, *args):
                self.executed.append((sql, args))
                if "INSERT INTO report_chunks" in sql:
                    # 두 번째 청크(chunk_index=1)만 충돌로 스킵.
                    return "INSERT 0 0" if args[1] == 1 else "INSERT 0 1"
                return "OK"

        conn = _SkipSecondConn()
        handler = ReportEmbedTaskHandler(
            connection=conn, settings=object(), storage=FakeStorage(), embedder=FakeEmbedder()
        )

        result = await handler({"task_context": {"raw_document_id": 42}})

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["chunks"], 2)  # 실제 적재 2 (한 건 스킵)
        self.assertEqual(result["attempted"], 3)  # 시도 3

    async def test_skips_when_not_parsed(self):
        conn = EmbedHandlerConn(parsing_status="pending")
        handler = ReportEmbedTaskHandler(connection=conn, settings=object(), embedder=FakeEmbedder())

        result = await handler({"task_context": {"raw_document_id": 42}})

        self.assertEqual(result["status"], "not_parsed")
        self.assertEqual(conn._chunk_inserts(), [])


if __name__ == "__main__":
    unittest.main()
