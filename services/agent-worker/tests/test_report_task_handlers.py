import sys
import unittest
import json
from datetime import date
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.report import tasks as report_tasks
from app.orchestrator.report.tasks import (
    ReportAnalyzeTaskHandler,
    ReportCollectTaskHandler,
    ReportNormalizeTaskHandler,
    ReportProcessTaskHandler,
)


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


# ── process_report ───────────────────────────────────────────────
class ProcessHandlerConn:
    def __init__(self):
        self.executed = []
        self.fetchvals = []
        self.valuation_facts = []

    async def fetchrow(self, sql, *args):
        if "JOIN report_raw_details" in sql:
            return {
                "stock_id": 1,
                "pdf_url": "https://example.com/report.pdf",
                "source_hash": "abcdef1234567890",
                "stock_code": "005930",
                "securities_firm": "Test Securities",
                "publish_date": "2026-06-24",
                "extra_payload": {"report_type": "cr"},
                "s3_key": None,
                "parsing_status": "pending",
            }
        if "INSERT INTO report_valuation_facts" in sql:
            self.valuation_facts.append(args)
            return {"raw_document_id": args[0], "stock_id": args[1]}
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
                "target_price": 120000,
                "valuation_facts": {
                    "target_price": 120000,
                    "forward_eps_est": 8000,
                    "eps_fy": 2026,
                    "methodology": "PER",
                    "applied_multiple": 15.0,
                    "implied_multiple": 15.0,
                    "peer_group": [],
                    "category_tag": None,
                    "rerating_thesis": None,
                    "extraction_source": "rules",
                    "needs_review": False,
                },
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
        self.assertEqual(observed["process"][0], "reports/005930/20260624_test_securities_abcdef12.pdf")
        self.assertTrue(any("UPDATE report_raw_details" in sql for sql, _ in conn.executed))
        self.assertEqual(conn.valuation_facts[0][2], "005930")
        self.assertEqual(conn.valuation_facts[0][6], 120000)
        self.assertEqual(conn.valuation_facts[0][7], 8000)
        self.assertEqual(conn.valuation_facts[0][9], "PER")
        self.assertEqual(conn.valuation_facts[0][10], 15.0)
        self.assertEqual(conn.valuation_facts[0][11], 15.0)
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
    async def test_normalizes_report_to_canonical_event_and_enqueues_analysis(self):
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
        self.assertEqual(result["analyze_task_id"], 701)
        self.assertTrue(conn._inserts("source_documents"))
        event_call = conn._inserts("signal_events")[0]
        self.assertEqual(event_call[2][3], "REPORT")
        self.assertEqual(event_call[2][4], "report_published")
        self.assertEqual(event_call[2][6], "positive")
        metric_names = [call[2][1] for call in conn._inserts("signal_metrics")]
        self.assertIn("report_target_price", metric_names)
        self.assertIn("report_upside_pct", metric_names)
        # NORMALIZE는 RAG 임베딩 없이 deterministic Report 분석 태스크만 인큐한다.
        enqueue_task_types = [
            call[2][1] for call in conn._enqueue_inserts() if len(call[2]) > 1
        ]
        self.assertNotIn("embed_report", enqueue_task_types)
        self.assertIn("analyze_report", enqueue_task_types)
        analyze_call = conn._enqueue_inserts()[0]
        self.assertEqual(analyze_call[2][4], [801])


# ── analyze_report ───────────────────────────────────────────────
class AnalyzeHandlerConn:
    def __init__(self):
        self.calls = []
        self.next_task_id = 701

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "FROM signal_events" in sql and "report_valuation_facts" in sql:
            return [
                {
                    "id": 801,
                    "stock_id": 1,
                    "event_date": date(2026, 6, 24),
                    "signal_direction": "positive",
                    "impact_level": "medium",
                    "title": "삼성전자 실적 점검",
                    "summary": "신한투자증권 리포트에서 확인된 데이터 방향성입니다.",
                    "evidence_url": "https://example.com/report.pdf",
                    "needs_review": False,
                    "raw_document_id": 42,
                    "ticker": "005930",
                    "broker": "신한투자증권",
                    "publish_date": date(2026, 6, 24),
                    "target_price": 90000,
                    "forward_eps_est": 6000,
                    "eps_fy": 2026,
                    "methodology": "PER",
                    "applied_multiple": 14.0,
                    "implied_multiple": 15.0,
                    "peer_group": ["SK하이닉스"],
                    "category_tag": "earnings",
                    "rerating_thesis": "실적 개선 근거",
                    "extraction_source": "rules",
                    "fact_needs_review": False,
                }
            ]
        return []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "INSERT INTO analysis_results" in sql:
            return {"id": 901}
        if "INSERT INTO agent_results" in sql:
            return {"id": 902}
        return None

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "SELECT id" in sql:
            return None
        if "INSERT INTO processing_queue" in sql:
            task_id = self.next_task_id
            self.next_task_id += 1
            return task_id
        return None

    def _insert(self, table):
        return next(call for call in self.calls if call[0] == "fetchrow" and f"INSERT INTO {table}" in call[1])

    def _enqueue(self, task_type):
        return next(
            call for call in self.calls
            if call[0] == "fetchval"
            and "INSERT INTO processing_queue" in call[1]
            and call[2][1] == task_type
        )


class ReportAnalyzeTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_persists_report_analysis_from_valuation_facts(self):
        conn = AnalyzeHandlerConn()
        handler = ReportAnalyzeTaskHandler(connection=conn)

        result = await handler(
            {
                "stock_id": 1,
                "source_signal_event_ids": [801],
                "task_context": {"stock_code": "005930", "run_key": "REPORT_EVENT_801"},
            }
        )

        self.assertEqual(result["analyzed_count"], 1)
        self.assertEqual(result["analysis_result_id"], 901)
        self.assertEqual(result["agent_result_id"], 902)
        self.assertEqual(result["aggregate_task_id"], 701)
        # 결정론 밸류에이션 신호: 투자의견(signal_direction="positive") 컨센서스 → positive, score 1.0.
        self.assertEqual(result["direction"], "positive")
        self.assertEqual(result["analysis_source"], "rules")
        self.assertFalse(result["needs_review"])

        analysis_call = conn._insert("analysis_results")
        self.assertEqual(analysis_call[2][2], date(2026, 6, 24))
        self.assertEqual(analysis_call[2][3], "REPORT_EVENT_801")
        self.assertEqual(analysis_call[2][4], [801])
        self.assertEqual(analysis_call[2][5], 100.0)  # _score_to_100(1.0) = 만장 긍정
        self.assertEqual(analysis_call[2][8], "full")

        agent_call = conn._insert("agent_results")
        self.assertEqual(agent_call[2][2], "D-1")
        self.assertEqual(agent_call[2][3], [801])
        self.assertEqual(agent_call[2][5], "positive")
        self.assertEqual(agent_call[2][10], "report-valuation-v1")
        method_detail = json.loads(agent_call[2][6])
        self.assertEqual(method_detail["source"], "REPORT")
        self.assertEqual(method_detail["data_status"], "ok")
        # 밸류에이션 피처는 보존된다(메타러너 입력, D1).
        self.assertEqual(method_detail["report_quant"]["valuation"]["target_price"], 90000)
        self.assertEqual(method_detail["report_quant"]["valuation"]["implied_multiple"], 15.0)
        self.assertEqual(method_detail["report_quant"]["valuation"]["needs_review"], False)

        # 주가 변동성 ML 채널 제거(C안): 분석 → AGGREGATE 직결. aggregate_ctx 는 task_context 최상위,
        # source_analysis_result_ids 는 enqueue 의 별도 컬럼으로 전달된다(레거시 단일 프로듀서 경로).
        enqueue_call = conn._enqueue("aggregate_signal")
        self.assertEqual(enqueue_call[2][0], 1)
        self.assertIn([901], enqueue_call[2])
        task_context = json.loads(enqueue_call[2][6])
        self.assertEqual(task_context["stock_code"], "005930")
        self.assertEqual(task_context["signal_date"], "2026-06-24")
        self.assertEqual(task_context["run_key"], "AGGREGATED")


class ReportConsensusDirectionTest(unittest.TestCase):
    """결정론 밸류에이션 컨센서스 — 투자의견 분포 → (direction, score, data_status)."""

    def _consensus(self, *directions):
        events = [{"signal_direction": d} for d in directions]
        return report_tasks._report_consensus_direction(events)

    def test_all_positive_is_positive_full_score(self):
        self.assertEqual(self._consensus("positive", "positive"), ("positive", 1.0, "ok"))

    def test_net_negative_is_negative(self):
        direction, score, status = self._consensus("negative", "negative", "positive")
        self.assertEqual(direction, "negative")
        self.assertEqual(status, "ok")
        self.assertLess(score, 0)

    def test_balanced_is_neutral(self):
        direction, score, status = self._consensus("positive", "negative")
        self.assertEqual(direction, "neutral")
        self.assertEqual(score, 0.0)
        self.assertEqual(status, "ok")

    def test_no_opinion_falls_back_to_no_signal(self):
        # 방향성 의견이 전혀 없으면 features-only 폴백(회귀 없음).
        self.assertEqual(self._consensus("unknown", "unknown"), ("unknown", 0.0, "no_signal"))
        self.assertEqual(self._consensus(), ("unknown", 0.0, "no_signal"))

    def test_neutral_opinions_count_as_directional(self):
        # 중립 의견은 방향성 건수에 포함되되 점수는 0 → neutral/ok.
        self.assertEqual(self._consensus("neutral", "neutral"), ("neutral", 0.0, "ok"))


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

    async def test_returns_collection_diagnostics_for_skipped_and_enqueued_reports(self):
        def _fake_collect_stock(**kwargs):
            return [
                {
                    "firm": "Test Securities",
                    "title": "Report with pdf",
                    "date": "2026.06.24",
                    "pdf_direct_url": "https://example.com/report.pdf",
                    "report_type": "cr",
                },
                {
                    "firm": "Test Securities",
                    "title": "Report without pdf",
                    "date": "2026.06.24",
                    "report_type": "cr",
                },
                {
                    "firm": "Test Securities",
                    "title": "Report without valid date",
                    "date": "not-a-date",
                    "pdf_direct_url": "https://example.com/invalid-date.pdf",
                    "report_type": "cr",
                },
            ]

        report_tasks.collect_stock = _fake_collect_stock
        conn = CollectHandlerConn()
        handler = ReportCollectTaskHandler(connection=conn, settings=None)

        result = await handler(
            {"stock_id": 1, "task_context": {"stock_code": "005930"}}
        )

        self.assertEqual(result["collected_reports"], 3)
        self.assertEqual(result["saved_reports"], 2)
        self.assertEqual(result["inserted_reports"], 2)
        self.assertEqual(result["duplicate_reports"], 0)
        self.assertEqual(result["invalid_date_reports"], 1)
        self.assertEqual(result["missing_pdf_reports"], 1)
        self.assertEqual(result["enqueued_reports"], 2)
        self.assertEqual(result["skip_reasons"], {"invalid_date": 1})

    async def test_logs_collection_diagnostics_summary(self):
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
        handler = ReportCollectTaskHandler(connection=CollectHandlerConn(), settings=None)

        with self.assertLogs("app.orchestrator.report.tasks", level="INFO") as cm:
            await handler({"stock_id": 1, "task_context": {"stock_code": "005930"}})

        self.assertTrue(
            any("report_collection_summary" in message for message in cm.output),
            cm.output,
        )

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
