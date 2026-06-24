import json
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
    ReportNormalizeTaskHandler,
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
        self.fetchvals = []

    async def fetchrow(self, sql, *args):
        return {
            "stock_id": 1,
            "stock_code": "005930",
            "s3_key": self._s3_key,
            "parsing_status": self._status,
        }

    async def execute(self, sql, *args):
        if "INSERT INTO report_chunks" in sql:
            self.inserts.append(args)

    async def fetchval(self, sql, *args):
        self.fetchvals.append((sql, args))
        if "SELECT id" in sql:
            return None
        return 701


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

    async def test_enqueues_report_analysis_with_signal_event_ids_after_embedding(self):
        conn = EmbedHandlerConn()
        handler = ReportEmbedTaskHandler(
            connection=conn,
            settings=None,
            storage=FakeStorage(pdf_bytes=b"%PDF-fake"),
        )

        result = await handler(
            {
                "source_signal_event_ids": [801],
                "task_context": {
                    "raw_document_id": 42,
                    "stock_code": "005930",
                    "run_key": "REPORT_EVENT_801",
                },
            }
        )

        self.assertEqual(result["analyze_task_id"], 701)
        enqueue = conn.fetchvals[-1][1]
        self.assertEqual(enqueue[1], "analyze_report")
        self.assertEqual(enqueue[4], [801])
        self.assertIn('"run_key": "REPORT_EVENT_801"', enqueue[6])


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

    async def test_default_storage_is_created_lazily(self):
        orig_factory = report_tasks.get_report_storage_client
        calls = []

        def _fail_if_called(settings):
            calls.append(settings)
            raise AssertionError("storage factory should not run during handler construction")

        report_tasks.get_report_storage_client = _fail_if_called
        try:
            ReportProcessTaskHandler(connection=ProcessHandlerConn(), settings=object())
            ReportEmbedTaskHandler(connection=EmbedHandlerConn(), settings=object())
        finally:
            report_tasks.get_report_storage_client = orig_factory

        self.assertEqual(calls, [])

    async def test_uses_injected_storage_client_for_download_parse_and_enqueue(self):
        conn = ProcessHandlerConn()
        storage = FakeStorage(exists=False)
        observed = {}

        def _fake_download(url, s3_key, passed_storage):
            observed["download"] = (url, s3_key, passed_storage)
            return True

        settings = object()

        def _fake_process(s3_key, passed_storage, *, settings=None):
            observed["process"] = (s3_key, passed_storage, settings)
            return {
                "opinion": "neutral",
                "target_price": 90000,
                "key_rationale": "근거",
                "raw_text": "본문",
            }

        report_tasks.download_and_upload = _fake_download
        report_tasks.process_from_s3 = _fake_process

        handler = ReportProcessTaskHandler(connection=conn, settings=settings, storage=storage)
        result = await handler({"task_context": {"raw_document_id": 42}})

        self.assertEqual(result["status"], "success")
        self.assertIs(observed["download"][2], storage)
        self.assertIs(observed["process"][1], storage)
        self.assertIs(observed["process"][2], settings)
        self.assertIn("reports/005930/", observed["process"][0])
        self.assertTrue(any("UPDATE report_raw_details" in sql for sql, _ in conn.executed))
        self.assertTrue(any(args[1] == "normalize_report" for _, args in conn.fetchvals))


# ── normalize_report ─────────────────────────────────────────────
class NormalizeHandlerConn:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "FROM report_raw_details" in sql:
            return [
                {
                    "raw_document_id": 42,
                    "stock_id": 1,
                    "source_type": "REPORT",
                    "source_name": "신한투자증권",
                    "title": "삼성전자 실적 점검",
                    "source_url": "https://example.com/report.pdf",
                    "published_at": datetime(2026, 6, 24),
                    "collected_at": datetime(2026, 6, 24, 1),
                    "securities_firm": "신한투자증권",
                    "publish_date": datetime(2026, 6, 24).date(),
                    "investment_opinion": "Buy",
                    "target_price": 90000,
                    "previous_target_price": 85000,
                    "current_price_at_publish": 75000,
                    "upside_pct": 20.0,
                    "key_rationale": "HBM 수요와 실적 개선 근거",
                    "extracted_text": "리포트 본문",
                }
            ]
        return []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "INSERT INTO source_documents" in sql:
            return {"id": 300}
        if "INSERT INTO signal_events" in sql:
            return {"id": 801}
        if "INSERT INTO signal_metrics" in sql:
            return {"id": 900}
        return None

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "SELECT id" in sql:
            return None
        if "INSERT INTO validation_logs" in sql:
            return 901
        return 701

    def _inserts(self, table):
        return [call for call in self.calls if call[0] == "fetchrow" and f"INSERT INTO {table}" in call[1]]

    def _enqueue_inserts(self):
        return [call for call in self.calls if call[0] == "fetchval" and "INSERT INTO processing_queue" in call[1]]


class ReportNormalizeTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_report_and_enqueues_embedding_with_event_id(self):
        conn = NormalizeHandlerConn()
        handler = ReportNormalizeTaskHandler(connection=conn)

        result = await handler(
            {
                "stock_id": 1,
                "source_raw_ids": [42],
                "task_context": {"stock_code": "005930"},
            }
        )

        self.assertEqual(result["normalized_count"], 1)
        self.assertEqual(result["signal_event_ids"], [801])
        self.assertTrue(conn._inserts("source_documents"))
        event_call = conn._inserts("signal_events")[0]
        self.assertEqual(event_call[2][3], "REPORT")
        self.assertEqual(event_call[2][4], "report_published")
        self.assertEqual(event_call[2][6], "positive")
        metric_names = [call[2][1] for call in conn._inserts("signal_metrics")]
        self.assertIn("report_target_price", metric_names)
        self.assertIn("report_upside_pct", metric_names)
        enqueue = conn._enqueue_inserts()[0][2]
        self.assertEqual(enqueue[1], "embed_report")
        self.assertEqual(enqueue[4], [801])
        self.assertIn('"raw_document_id": 42', enqueue[6])


# ── analyze_report ──────────────────────────────────────────────
class AnalyzeHandlerConn:
    def __init__(self):
        self.upserts = []
        self.method_signal = None
        self.analysis_args = None
        self.agent_args = None
        self.fetchvals = []

    async def fetch(self, sql, *args):
        if "report_raw_details" in sql:
            return [
                {"investment_opinion": "Buy", "target_price": 90000},
                {"investment_opinion": "Buy", "target_price": 100000},
                {"investment_opinion": "Hold", "target_price": None},
            ]
        if "FROM signal_events" in sql:
            return [
                {
                    "id": 801,
                    "stock_id": 1,
                    "event_date": datetime(2026, 6, 24).date(),
                    "title": "삼성전자 실적 점검",
                    "summary": "데이터 방향성 확인",
                }
            ]
        return []

    async def fetchrow(self, sql, *args):
        if "INSERT INTO analysis_results" in sql:
            self.upserts.append("analysis_results")
            self.analysis_args = args
            return {"id": 100}
        if "INSERT INTO agent_results" in sql:
            self.upserts.append("agent_results")
            # upsert_agent_result: $1 result_id, $2 stock_id, $3 debate_method,
            # $4 source_signal_event_ids, $5 method_score, $6 method_signal
            self.method_signal = args[5]
            self.agent_args = args
            return {"id": 200}
        return None

    async def fetchval(self, sql, *args):
        self.fetchvals.append((sql, args))
        if "SELECT id" in sql:
            return None
        return 601


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
            {
                "stock_id": 1,
                "source_signal_event_ids": [801],
                "task_context": {"stock_code": "005930", "run_key": "REPORT_EVENT_801"},
            }
        )

        self.assertEqual(result["analysis_result_id"], 100)
        self.assertEqual(result["agent_result_id"], 200)
        self.assertEqual(result["direction"], "positive")
        # 저장 순서: analysis_results → agent_results
        self.assertEqual(conn.upserts, ["analysis_results", "agent_results"])
        self.assertEqual(conn.analysis_args[4], [801])
        self.assertEqual(conn.analysis_args[8], "quick")
        self.assertEqual(conn.agent_args[3], [801])
        # 정량 집계: 평균 목표주가, 의견 충돌 감지
        quant = result["report_quant"]
        self.assertEqual(quant["report_count"], 3)
        self.assertEqual(quant["avg_target"], 95000)
        self.assertTrue(quant["conflict_detected"])
        # method_signal은 허용값(positive). agent에 정량이 context로 주입됐는지
        self.assertEqual(conn.method_signal, "positive")
        self.assertEqual(agent.received.context["report_quant"]["avg_target"], 95000)
        self.assertEqual(agent.received.events[0]["id"], 801)
        self.assertEqual(result["ml_infer_task_id"], 601)
        ml_enqueue = next(call for call in conn.fetchvals if "INSERT INTO processing_queue" in call[0])
        self.assertEqual(ml_enqueue[1][1], "ml_infer")
        self.assertEqual(ml_enqueue[1][5], [100])
        ml_context = json.loads(ml_enqueue[1][6])
        self.assertEqual(ml_context["run_key"], "ML")
        self.assertEqual(
            ml_context["aggregate_ctx"]["aggregation_key"],
            "AGGREGATED:1:2026-06-24:final-agg-v1",
        )
        self.assertEqual(ml_context["aggregate_ctx"]["source_analysis_result_ids"], [100])

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


# ── collect_report ───────────────────────────────────────────────
class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class CollectHandlerConn:
    def __init__(self):
        self.calls = []
        self.next_raw_id = 42

    def transaction(self):
        return FakeTransaction()

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "INSERT INTO collector_runs" in sql:
            return 900
        if "SELECT id" in sql:
            return None
        if "INSERT INTO processing_queue" in sql:
            return 501
        return 1

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "INSERT INTO raw_documents" in sql:
            raw_id = self.next_raw_id
            self.next_raw_id += 1
            return {"id": raw_id}
        if "SELECT id FROM raw_documents" in sql:
            return {"id": self.next_raw_id}
        return None

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "OK"

    def _collector_finish_calls(self):
        return [
            call for call in self.calls
            if call[0] == "execute" and "UPDATE collector_runs" in call[1]
        ]


class StrictCollectConn:
    async def fetchrow(self, sql, *args):
        raise AssertionError(f"unexpected direct fetchrow: {sql}")

    async def execute(self, sql, *args):
        raise AssertionError(f"unexpected direct execute: {sql}")

    async def fetchval(self, sql, *args):
        if "SELECT id" in sql:
            return None
        if "INSERT INTO processing_queue" in sql:
            return 501
        raise AssertionError(f"unexpected direct fetchval: {sql}")

    def transaction(self):
        raise AssertionError("Report collection persistence should use CollectionRepository")


class FakeCollectionRepository:
    instances = []

    def __init__(self, connection):
        self.connection = connection
        self.calls = []
        FakeCollectionRepository.instances.append(self)

    async def create_collector_run(self, collector_type, run_mode):
        self.calls.append(("create_collector_run", collector_type, run_mode))
        return 900

    async def finish_collector_run(self, **kwargs):
        self.calls.append(("finish_collector_run", kwargs))

    async def upsert_raw_document(self, **kwargs):
        self.calls.append(("upsert_raw_document", kwargs))
        return {"id": 42, "inserted": True}

    async def upsert_report_detail(self, **kwargs):
        self.calls.append(("upsert_report_detail", kwargs))
        return {"raw_document_id": kwargs["raw_document_id"]}


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
        handler = ReportCollectTaskHandler(connection=CollectHandlerConn(), settings=None)
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
        handler = ReportCollectTaskHandler(connection=CollectHandlerConn(), settings=None)
        await handler(
            {"stock_id": 1, "task_context": {"stock_code": "005930", "days_back": 7}}
        )
        delta = self.captured["date_end"] - self.captured["date_start"]
        self.assertEqual(delta.days, 7)
        self.assertEqual(self.captured["max_pages"], 20)


class ReportCollectRunLoggingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig = report_tasks.collect_stock

    def tearDown(self):
        report_tasks.collect_stock = self._orig

    async def test_records_successful_collector_run(self):
        def _fake_collect_stock(**kwargs):
            return [
                {
                    "firm": "Test Securities",
                    "title": "Report title",
                    "date": "2026.06.24",
                    "pdf_direct_url": "https://example.com/report.pdf",
                    "report_type": "cr",
                }
            ]

        report_tasks.collect_stock = _fake_collect_stock
        conn = CollectHandlerConn()
        handler = ReportCollectTaskHandler(connection=conn, settings=None)

        result = await handler(
            {"stock_id": 1, "task_context": {"stock_code": "005930"}}
        )

        self.assertEqual(result["collector_run_id"], 900)
        self.assertEqual(result["collected"], 1)
        finish = conn._collector_finish_calls()[0]
        self.assertEqual(finish[2][0], 900)
        self.assertEqual(finish[2][1], "success")
        self.assertEqual(finish[2][2], 1)
        self.assertEqual(finish[2][3], 1)
        self.assertEqual(finish[2][5], 0)
        raw_insert = next(call for call in conn.calls if "INSERT INTO raw_documents" in call[1])
        self.assertIn("collector_run_id", raw_insert[1])
        self.assertEqual(raw_insert[2][1], 900)

    async def test_records_failed_collector_run_when_collection_raises(self):
        def _raise_collect_stock(**kwargs):
            raise RuntimeError("crawler failed")

        report_tasks.collect_stock = _raise_collect_stock
        conn = CollectHandlerConn()
        handler = ReportCollectTaskHandler(connection=conn, settings=None)

        with self.assertRaisesRegex(RuntimeError, "crawler failed"):
            await handler({"stock_id": 1, "task_context": {"stock_code": "005930"}})

        finish = conn._collector_finish_calls()[0]
        self.assertEqual(finish[2][0], 900)
        self.assertEqual(finish[2][1], "failed")
        self.assertEqual(finish[2][5], 1)
        self.assertIn("crawler failed", finish[2][6])


class ReportCollectRepositoryPersistenceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_collect_stock = report_tasks.collect_stock
        self._orig_collection_repository = report_tasks.CollectionRepository
        FakeCollectionRepository.instances = []

    def tearDown(self):
        report_tasks.collect_stock = self._orig_collect_stock
        report_tasks.CollectionRepository = self._orig_collection_repository

    async def test_persists_reports_through_collection_repository(self):
        def _fake_collect_stock(**kwargs):
            return [
                {
                    "firm": "Test Securities",
                    "title": "Report title",
                    "date": "2026.06.24",
                    "pdf_direct_url": "https://example.com/report.pdf",
                    "report_type": "cr",
                }
            ]

        report_tasks.collect_stock = _fake_collect_stock
        report_tasks.CollectionRepository = FakeCollectionRepository

        handler = ReportCollectTaskHandler(connection=StrictCollectConn(), settings=None)
        result = await handler({"stock_id": 1, "task_context": {"stock_code": "005930"}})

        self.assertEqual(result["collected"], 1)
        repo = FakeCollectionRepository.instances[0]
        raw_call = next(call for call in repo.calls if call[0] == "upsert_raw_document")
        detail_call = next(call for call in repo.calls if call[0] == "upsert_report_detail")
        self.assertEqual(raw_call[1]["collector_run_id"], 900)
        self.assertEqual(raw_call[1]["source_type"], "REPORT")
        self.assertEqual(raw_call[1]["source_name"], "Test Securities")
        self.assertEqual(raw_call[1]["external_id"], raw_call[1]["source_hash"])
        self.assertEqual(detail_call[1]["raw_document_id"], 42)
        self.assertEqual(detail_call[1]["securities_firm"], "Test Securities")
        self.assertEqual(detail_call[1]["parsing_status"], "pending")
        self.assertEqual(detail_call[1]["extra_payload"], {"report_type": "cr"})


if __name__ == "__main__":
    unittest.main()
