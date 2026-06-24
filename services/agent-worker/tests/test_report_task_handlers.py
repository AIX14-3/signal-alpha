import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.agents.base import SourceAgentOutput
from app.embeddings.provider import EMBEDDING_DIM, set_embedding_provider
from app.orchestrator.report import tasks as report_tasks
from app.orchestrator.report.tasks import (
    ReportAnalyzeTaskHandler,
    ReportCollectTaskHandler,
    ReportEmbedTaskHandler,
    ReportProcessTaskHandler,
)


class FakeProvider:
    async def embed(self, texts):
        return [[0.5] * EMBEDDING_DIM for _ in texts]


class FakeStorage:
    def __init__(self, *, exists=True, pdf_bytes=b"%PDF-fake"):
        self.exists_value = exists
        self.pdf_bytes = pdf_bytes
        self.downloaded_keys = []

    def exists(self, key):
        return self.exists_value

    def upload_pdf(self, pdf_bytes, key):
        self.pdf_bytes = pdf_bytes
        self.exists_value = True
        return key

    def download_pdf(self, key):
        self.downloaded_keys.append(key)
        return self.pdf_bytes


# ── embed_report ────────────────────────────────────────────────
class EmbedHandlerConn:
    def __init__(self, parsing_status="success", s3_key="reports/005930/x.pdf"):
        self._status = parsing_status
        self._s3_key = s3_key
        self.inserts = []

    async def fetchrow(self, sql, *args):
        return {"stock_id": 1, "s3_key": self._s3_key, "parsing_status": self._status}

    async def execute(self, sql, *args):
        if "INSERT INTO report_chunks" in sql:
            self.inserts.append(args)


class ReportEmbedTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        set_embedding_provider(FakeProvider())
        self._orig_extract = report_tasks.extract_text
        report_tasks.extract_text = lambda b: ("문단입니다. " * 250)

    def tearDown(self):
        set_embedding_provider(None)
        report_tasks.extract_text = self._orig_extract

    async def test_embeds_full_text_and_inserts_chunks(self):
        conn = EmbedHandlerConn()
        handler = ReportEmbedTaskHandler(
            connection=conn,
            settings=None,
            storage=FakeStorage(pdf_bytes=b"%PDF-fake"),
        )

        result = await handler({"task_context": {"raw_document_id": 42}})

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(conn.inserts), result["chunks"])
        # 마지막 인자(embedding)는 1024차원 pgvector 문자열
        emb = conn.inserts[0][-1]
        self.assertTrue(isinstance(emb, str) and emb.startswith("["))
        self.assertEqual(emb.count(",") + 1, EMBEDDING_DIM)
        # chunk_index 순차
        self.assertEqual([a[2] for a in conn.inserts], list(range(len(conn.inserts))))

    async def test_not_ready_when_parsing_incomplete(self):
        conn = EmbedHandlerConn(parsing_status="pending", s3_key=None)
        handler = ReportEmbedTaskHandler(
            connection=conn,
            settings=None,
            storage=FakeStorage(),
        )
        result = await handler({"task_context": {"raw_document_id": 7}})
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(conn.inserts, [])

    async def test_uses_injected_storage_client(self):
        conn = EmbedHandlerConn()
        storage = FakeStorage(pdf_bytes=b"%PDF-from-storage")
        handler = ReportEmbedTaskHandler(connection=conn, settings=None, storage=storage)

        result = await handler({"task_context": {"raw_document_id": 42}})

        self.assertEqual(result["status"], "success")
        self.assertEqual(storage.downloaded_keys, ["reports/005930/x.pdf"])


# ── process_report ───────────────────────────────────────────────
class ProcessHandlerConn:
    def __init__(self):
        self.executed = []
        self.fetchvals = []

    async def fetchrow(self, sql, *args):
        if "JOIN report_raw_details" in sql:
            return {
                "stock_id": 1,
                "pdf_url": "https://example.com/report.pdf",
                "stock_code": "005930",
                "securities_firm": "Test Securities",
                "publish_date": "2026-06-24",
                "extra_payload": {"report_type": "cr"},
                "s3_key": None,
                "parsing_status": "pending",
            }
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetchval(self, sql, *args):
        self.fetchvals.append((sql, args))
        if "SELECT id" in sql:
            return None
        return 501


class ReportProcessTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_download = report_tasks.download_and_upload
        self._orig_process = report_tasks.process_from_s3

    def tearDown(self):
        report_tasks.download_and_upload = self._orig_download
        report_tasks.process_from_s3 = self._orig_process

    async def test_uses_injected_storage_client_for_download_parse_and_enqueue(self):
        conn = ProcessHandlerConn()
        storage = FakeStorage(exists=False)
        observed = {}

        def _fake_download(url, s3_key, passed_storage):
            observed["download"] = (url, s3_key, passed_storage)
            return True

        def _fake_process(s3_key, passed_storage):
            observed["process"] = (s3_key, passed_storage)
            return {
                "opinion": "neutral",
                "target_price": 90000,
                "key_rationale": "근거",
                "raw_text": "본문",
            }

        report_tasks.download_and_upload = _fake_download
        report_tasks.process_from_s3 = _fake_process

        handler = ReportProcessTaskHandler(connection=conn, settings=None, storage=storage)
        result = await handler({"task_context": {"raw_document_id": 42}})

        self.assertEqual(result["status"], "success")
        self.assertIs(observed["download"][2], storage)
        self.assertIs(observed["process"][1], storage)
        self.assertIn("reports/005930/", observed["process"][0])
        self.assertTrue(any("UPDATE report_raw_details" in sql for sql, _ in conn.executed))
        self.assertTrue(any(args[1] == "embed_report" for _, args in conn.fetchvals))


# ── analyze_report ──────────────────────────────────────────────
class AnalyzeHandlerConn:
    def __init__(self):
        self.upserts = []
        self.method_signal = None

    async def fetch(self, sql, *args):
        if "report_raw_details" in sql:
            return [
                {"investment_opinion": "Buy", "target_price": 90000},
                {"investment_opinion": "Buy", "target_price": 100000},
                {"investment_opinion": "Hold", "target_price": None},
            ]
        return []

    async def fetchrow(self, sql, *args):
        if "INSERT INTO analysis_results" in sql:
            self.upserts.append("analysis_results")
            return {"id": 100}
        if "INSERT INTO agent_results" in sql:
            self.upserts.append("agent_results")
            # upsert_agent_result: $1 result_id, $2 stock_id, $3 debate_method,
            # $4 source_signal_event_ids, $5 method_score, $6 method_signal
            self.method_signal = args[5]
            return {"id": 200}
        return None


class FakeAgent:
    def __init__(self, direction="positive"):
        self._direction = direction

    async def analyze(self, input_data):
        self.received = input_data
        return SourceAgentOutput(
            source="REPORT",
            stock_code=input_data.stock_code,
            direction=self._direction,
            score=68.0,
            summary="OK",
            risk_flags=["risk1"],
            method_detail={"coverage": {"firms": ["a", "b", "c"]}, "evidence_chunks": [{"raw_document_id": 1}]},
            needs_review=False,
            data_status="ok",
            analysis_source="llm",
            llm_model="m",
            prompt_ver="report-rag-v1",
        )


class ReportAnalyzeTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_builds_quant_and_persists_analysis_and_agent_results(self):
        conn = AnalyzeHandlerConn()
        agent = FakeAgent()
        handler = ReportAnalyzeTaskHandler(connection=conn, settings=None, analysis_agent=agent)

        result = await handler(
            {"stock_id": 1, "task_context": {"stock_code": "005930", "run_key": "REPORT"}}
        )

        self.assertEqual(result["analysis_result_id"], 100)
        self.assertEqual(result["agent_result_id"], 200)
        self.assertEqual(result["direction"], "positive")
        # 저장 순서: analysis_results → agent_results
        self.assertEqual(conn.upserts, ["analysis_results", "agent_results"])
        # 정량 집계: 평균 목표주가, 의견 충돌 감지
        quant = result["report_quant"]
        self.assertEqual(quant["report_count"], 3)
        self.assertEqual(quant["avg_target"], 95000)
        self.assertTrue(quant["conflict_detected"])
        # method_signal은 허용값(positive). agent에 정량이 context로 주입됐는지
        self.assertEqual(conn.method_signal, "positive")
        self.assertEqual(agent.received.context["report_quant"]["avg_target"], 95000)

    async def test_unknown_direction_is_mapped_to_neutral_for_method_signal(self):
        # agent_results.method_signal CHECK는 unknown 불가 → neutral로 매핑돼야 함
        conn = AnalyzeHandlerConn()
        handler = ReportAnalyzeTaskHandler(
            connection=conn, settings=None, analysis_agent=FakeAgent(direction="unknown")
        )
        result = await handler(
            {"stock_id": 1, "task_context": {"stock_code": "005930"}}
        )
        self.assertEqual(conn.method_signal, "neutral")
        self.assertEqual(result["direction"], "unknown")  # method_detail/반환은 원본 유지


# ── collect_report 날짜 해석 ─────────────────────────────────────
class ReportCollectDateResolutionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig = report_tasks.collect_stock
        self.captured = {}

        def _fake_collect_stock(**kwargs):
            self.captured.update(kwargs)
            return []  # 빈 결과 → _save_to_db/enqueue 미수행

        report_tasks.collect_stock = _fake_collect_stock

    def tearDown(self):
        report_tasks.collect_stock = self._orig

    async def test_absolute_dates_take_priority(self):
        handler = ReportCollectTaskHandler(connection=object(), settings=None)
        await handler(
            {
                "stock_id": 1,
                "task_context": {
                    "stock_code": "005930",
                    "date_start": "2025-01-01",
                    "date_end": "2025-12-31",
                    "max_pages": 100,
                },
            }
        )
        self.assertEqual(self.captured["date_start"], datetime(2025, 1, 1))
        self.assertEqual(self.captured["date_end"], datetime(2025, 12, 31))
        self.assertEqual(self.captured["max_pages"], 100)

    async def test_days_back_fallback(self):
        handler = ReportCollectTaskHandler(connection=object(), settings=None)
        await handler(
            {"stock_id": 1, "task_context": {"stock_code": "005930", "days_back": 7}}
        )
        delta = self.captured["date_end"] - self.captured["date_start"]
        self.assertEqual(delta.days, 7)
        self.assertEqual(self.captured["max_pages"], 20)


if __name__ == "__main__":
    unittest.main()
