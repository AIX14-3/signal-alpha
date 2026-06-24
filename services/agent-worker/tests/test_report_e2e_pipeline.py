import json
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.agents.base import SourceAgentOutput
from app.embeddings.provider import EMBEDDING_DIM, set_embedding_provider
from app.ml.inference import MlInferTaskHandler
from app.orchestrator.queue.task_types import (
    AGGREGATE_SIGNAL,
    ANALYZE_REPORT,
    COLLECT_REPORT,
    EMBED_REPORT,
    ML_INFER,
    NORMALIZE_REPORT,
    PROCESS_REPORT,
)
from app.orchestrator.queue.tasks import QueueTaskRunner
from app.orchestrator.report import tasks as report_tasks
from app.orchestrator.report.tasks import (
    ReportAnalyzeTaskHandler,
    ReportCollectTaskHandler,
    ReportEmbedTaskHandler,
    ReportNormalizeTaskHandler,
    ReportProcessTaskHandler,
)


class FakeEmbeddingProvider:
    async def embed(self, texts):
        return [[0.25] * EMBEDDING_DIM for _ in texts]


class FakeReportStorage:
    def __init__(self):
        self.objects = {}

    def exists(self, key):
        return key in self.objects

    def upload_pdf(self, pdf_bytes, key):
        self.objects[key] = pdf_bytes
        return key

    def download_pdf(self, key):
        return self.objects[key]


class FakeReportAgent:
    async def analyze(self, input_data):
        self.received = input_data
        return SourceAgentOutput(
            source="REPORT",
            stock_code=input_data.stock_code,
            direction="neutral",
            score=55.0,
            summary="데이터 방향성과 근거를 확인했습니다.",
            risk_flags=[],
            method_detail={
                "coverage": {"firms": ["Test Securities"]},
                "evidence_chunks": [{"raw_document_id": 42, "chunk_index": 0}],
            },
            needs_review=False,
            data_status="ok",
            analysis_source="rules_fallback",
            llm_model=None,
            prompt_ver="report-rag-v1",
        )


class FakeReportPipelineConnection:
    def __init__(self):
        self.collector_runs = []
        self.raw_documents = {}
        self.report_raw_details = {}
        self.source_documents = {}
        self.signal_events = {}
        self.signal_metrics = []
        self.validation_logs = []
        self.report_chunks = []
        self.analysis_results = {}
        self.agent_results = {}
        self.processing_queue = []
        self.finished_collector_runs = []
        self.calls = []
        self._next = {
            "collector_run": 900,
            "raw_document": 42,
            "source_document": 300,
            "signal_event": 801,
            "signal_metric": 901,
            "validation_log": 950,
            "analysis_result": 100,
            "agent_result": 200,
            "queue": 500,
        }

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "INSERT INTO collector_runs" in sql:
            run_id = self._take("collector_run")
            self.collector_runs.append(
                {"id": run_id, "collector_type": args[0], "run_mode": args[1]}
            )
            return run_id
        if "SELECT id" in sql and "FROM processing_queue" in sql:
            return None
        if "INSERT INTO processing_queue" in sql:
            task_id = self._take("queue")
            self.processing_queue.append(
                {
                    "id": task_id,
                    "stock_id": args[0],
                    "task_type": args[1],
                    "priority": args[2],
                    "source_raw_ids": args[3],
                    "source_signal_event_ids": args[4],
                    "source_analysis_result_ids": args[5],
                    "task_context": args[6],
                    "status": "pending",
                    "retry_count": 0,
                    "max_retry_count": 3,
                }
            )
            return task_id
        if "INSERT INTO validation_logs" in sql:
            log_id = self._take("validation_log")
            self.validation_logs.append(
                {
                    "id": log_id,
                    "target_type": args[0],
                    "target_id_int": args[1],
                    "validation_type": args[3],
                    "passed": args[4],
                    "message": args[5],
                }
            )
            return log_id
        raise AssertionError(f"unexpected fetchval SQL: {sql}")

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "UPDATE processing_queue" in sql and "RETURNING *" in sql:
            return self._claim_task(sql, args)
        if "INSERT INTO raw_documents" in sql:
            raw_document_id = self._take("raw_document")
            row = {
                "id": raw_document_id,
                "stock_id": args[0],
                "collector_run_id": args[1],
                "source_type": args[2],
                "source_name": args[3],
                "external_id": args[4],
                "source_hash": args[5],
                "title": args[6],
                "source_url": args[7],
                "published_at": args[8],
                "collected_at": datetime(2026, 6, 24, 9),
                "inserted": True,
            }
            self.raw_documents[raw_document_id] = row
            return row
        if "INSERT INTO report_raw_details" in sql:
            detail = {
                "raw_document_id": args[0],
                "stock_id": args[1],
                "securities_firm": args[2],
                "analyst_name": args[3],
                "publish_date": args[4],
                "investment_opinion": args[5],
                "target_price": args[6],
                "previous_target_price": args[7],
                "current_price_at_publish": args[8],
                "upside_pct": args[9],
                "has_pdf": args[10],
                "pdf_url": args[11],
                "local_file_path": args[12],
                "extracted_text": args[13],
                "extracted_text_path": args[14],
                "parsing_status": args[15],
                "parsing_error": args[16],
                "extra_payload": args[17],
                "s3_key": None,
                "key_rationale": None,
            }
            self.report_raw_details[args[0]] = detail
            return detail
        if "JOIN report_raw_details" in sql and "rd.source_url" in sql:
            return self._process_row(args[0])
        if "JOIN report_raw_details" in sql and "rrd.s3_key" in sql:
            return self._embed_row(args[0])
        if "INSERT INTO source_documents" in sql:
            source_document_id = self._take("source_document")
            row = {
                "id": source_document_id,
                "raw_document_id": args[0],
                "stock_id": args[1],
                "source_type": args[2],
                "source_name": args[3],
                "title": args[4],
                "source_url": args[5],
                "published_at": args[6],
                "collected_at": args[7],
                "reliability_level": args[8],
                "is_official": args[9],
            }
            self.source_documents[source_document_id] = row
            return row
        if "INSERT INTO signal_events" in sql:
            signal_event_id = self._take("signal_event")
            row = {
                "id": signal_event_id,
                "stock_id": args[0],
                "source_document_id": args[1],
                "event_hash": args[2],
                "source_type": args[3],
                "event_type": args[4],
                "event_date": args[5],
                "signal_direction": args[6],
                "impact_level": args[7],
                "title": args[8],
                "summary": args[9],
                "evidence_text": args[10],
                "evidence_url": args[11],
                "needs_review": args[12],
            }
            self.signal_events[signal_event_id] = row
            return row
        if "INSERT INTO signal_metrics" in sql:
            metric_id = self._take("signal_metric")
            row = {
                "id": metric_id,
                "signal_event_id": args[0],
                "metric_name": args[1],
                "metric_value": args[2],
                "metric_unit": args[3],
            }
            self.signal_metrics.append(row)
            return row
        if "INSERT INTO analysis_results" in sql:
            analysis_result_id = self._take("analysis_result")
            row = {
                "id": analysis_result_id,
                "request_id": args[0],
                "stock_id": args[1],
                "analysis_date": args[2],
                "run_key": args[3],
                "source_signal_event_ids": args[4],
                "base_score": args[5],
                "analysis_mode": args[8],
                "warning": args[9],
                "version": args[11],
            }
            self.analysis_results[analysis_result_id] = row
            return row
        if "INSERT INTO agent_results" in sql:
            agent_result_id = self._take("agent_result")
            row = {
                "id": agent_result_id,
                "result_id": args[0],
                "stock_id": args[1],
                "debate_method": args[2],
                "source_signal_event_ids": args[3],
                "method_score": args[4],
                "method_signal": args[5],
                "method_detail": json.loads(args[6]),
                "reliability_score": args[7],
                "evidence_quality": args[8],
                "llm_model": args[9],
                "prompt_ver": args[10],
            }
            self.agent_results[agent_result_id] = row
            return row
        raise AssertionError(f"unexpected fetchrow SQL: {sql}")

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "FROM report_raw_details" in sql and "INNER JOIN raw_documents" in sql:
            raw_ids = set(args[0])
            return [
                self._report_detail_row(raw_document_id)
                for raw_document_id in raw_ids
                if self.report_raw_details[raw_document_id]["parsing_status"] == "success"
            ]
        if "FROM signal_events" in sql and "INNER JOIN source_documents" in sql:
            signal_event_ids = set(args[0])
            return [
                self._signal_event_join_row(signal_event_id)
                for signal_event_id in signal_event_ids
                if signal_event_id in self.signal_events
            ]
        if "FROM report_raw_details" in sql and "parsing_status = 'success'" in sql:
            stock_id = args[0]
            return [
                {
                    "investment_opinion": detail["investment_opinion"],
                    "target_price": detail["target_price"],
                }
                for detail in self.report_raw_details.values()
                if detail["stock_id"] == stock_id and detail["parsing_status"] == "success"
            ]
        if "FROM ohlcv_data" in sql:
            return []
        raise AssertionError(f"unexpected fetch SQL: {sql}")

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        if "UPDATE processing_queue" in sql and "status = 'success'" in sql:
            task = self._task_by_id(args[0])
            task["status"] = "success"
            return "UPDATE 1"
        if "UPDATE collector_runs" in sql:
            self.finished_collector_runs.append(
                {
                    "run_id": args[0],
                    "status": args[1],
                    "collected_count": args[2],
                    "inserted_count": args[3],
                    "skipped_count": args[4],
                    "failed_count": args[5],
                    "error_message": args[6],
                }
            )
            return "UPDATE 1"
        if "UPDATE report_raw_details" in sql and "parsing_status     = 'success'" in sql:
            detail = self.report_raw_details[args[0]]
            detail.update(
                {
                    "s3_key": args[1],
                    "has_pdf": True,
                    "parsing_status": "success",
                    "investment_opinion": args[2],
                    "target_price": args[3],
                    "key_rationale": args[4],
                    "extracted_text": args[5],
                }
            )
            return "UPDATE 1"
        if "INSERT INTO report_chunks" in sql:
            self.report_chunks.append(
                {
                    "raw_document_id": args[0],
                    "stock_id": args[1],
                    "chunk_index": args[2],
                    "chunk_text": args[3],
                    "token_count": args[4],
                    "embedding": args[5],
                }
            )
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute SQL: {sql}")

    def _take(self, key):
        value = self._next[key]
        self._next[key] += 1
        return value

    def seed_task(
        self,
        *,
        task_type,
        stock_id,
        task_context=None,
        source_raw_ids=None,
        source_signal_event_ids=None,
        source_analysis_result_ids=None,
    ):
        task_id = self._take("queue")
        self.processing_queue.append(
            {
                "id": task_id,
                "stock_id": stock_id,
                "task_type": task_type,
                "priority": "batch",
                "source_raw_ids": source_raw_ids,
                "source_signal_event_ids": source_signal_event_ids,
                "source_analysis_result_ids": source_analysis_result_ids,
                "task_context": task_context,
                "status": "pending",
                "retry_count": 0,
                "max_retry_count": 3,
            }
        )
        return task_id

    def _claim_task(self, sql, args):
        if "WHERE id = $1" in sql:
            task_id, task_type = args[0], args[1]
            candidates = [
                task for task in self.processing_queue
                if task["id"] == task_id and task["task_type"] == task_type
            ]
        else:
            task_type = args[0]
            candidates = [
                task for task in self.processing_queue
                if task["task_type"] == task_type and task["status"] in {"pending", "retrying"}
            ]
        if not candidates:
            return None
        task = candidates[0]
        task["status"] = "running"
        return dict(task)

    def _task_by_id(self, task_id):
        return next(task for task in self.processing_queue if task["id"] == task_id)

    def task(self, task_type):
        return next(task for task in self.processing_queue if task["task_type"] == task_type)

    def task_types(self):
        return [task["task_type"] for task in self.processing_queue]

    def _process_row(self, raw_document_id):
        raw = self.raw_documents[raw_document_id]
        detail = self.report_raw_details[raw_document_id]
        return {
            "stock_id": raw["stock_id"],
            "pdf_url": raw["source_url"],
            "stock_code": "005930",
            "securities_firm": detail["securities_firm"],
            "publish_date": detail["publish_date"],
            "extra_payload": detail["extra_payload"],
            "s3_key": detail["s3_key"],
            "parsing_status": detail["parsing_status"],
        }

    def _embed_row(self, raw_document_id):
        raw = self.raw_documents[raw_document_id]
        detail = self.report_raw_details[raw_document_id]
        return {
            "stock_id": raw["stock_id"],
            "stock_code": "005930",
            "s3_key": detail["s3_key"],
            "parsing_status": detail["parsing_status"],
        }

    def _report_detail_row(self, raw_document_id):
        raw = self.raw_documents[raw_document_id]
        detail = self.report_raw_details[raw_document_id]
        return {
            "raw_document_id": raw_document_id,
            "stock_id": raw["stock_id"],
            "source_type": raw["source_type"],
            "source_name": raw["source_name"],
            "title": raw["title"],
            "source_url": raw["source_url"],
            "published_at": raw["published_at"],
            "collected_at": raw["collected_at"],
            "securities_firm": detail["securities_firm"],
            "publish_date": detail["publish_date"],
            "investment_opinion": detail["investment_opinion"],
            "target_price": detail["target_price"],
            "previous_target_price": detail["previous_target_price"],
            "current_price_at_publish": detail["current_price_at_publish"],
            "upside_pct": detail["upside_pct"],
            "key_rationale": detail["key_rationale"],
            "extracted_text": detail["extracted_text"],
        }

    def _signal_event_join_row(self, signal_event_id):
        event = dict(self.signal_events[signal_event_id])
        source_document = self.source_documents[event["source_document_id"]]
        event.update(
            {
                "source_name": source_document["source_name"],
                "source_url": source_document["source_url"],
                "published_at": source_document["published_at"],
                "reliability_level": source_document["reliability_level"],
                "is_official": source_document["is_official"],
            }
        )
        return event


class ReportE2EPipelineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        set_embedding_provider(FakeEmbeddingProvider())
        self._orig_collect_stock = report_tasks.collect_stock
        self._orig_download = report_tasks.download_and_upload
        self._orig_process = report_tasks.process_from_s3
        self._orig_extract = report_tasks.extract_text

    def tearDown(self):
        set_embedding_provider(None)
        report_tasks.collect_stock = self._orig_collect_stock
        report_tasks.download_and_upload = self._orig_download
        report_tasks.process_from_s3 = self._orig_process
        report_tasks.extract_text = self._orig_extract

    async def test_report_queue_pipeline_preserves_canonical_links_to_ml_infer(self):
        conn = FakeReportPipelineConnection()
        storage = FakeReportStorage()
        agent = FakeReportAgent()

        report_tasks.collect_stock = lambda **kwargs: [
            {
                "firm": "Test Securities",
                "title": "005930 데이터 방향성 점검",
                "date": "2026.06.24",
                "pdf_direct_url": "https://example.com/report.pdf",
                "report_type": "cr",
            }
        ]
        report_tasks.download_and_upload = lambda url, key, passed_storage: bool(
            passed_storage.upload_pdf(b"%PDF-fake", key)
        )
        report_tasks.process_from_s3 = lambda key, passed_storage, *, settings=None: {
            "opinion": "neutral",
            "target_price": 90000,
            "key_rationale": "실적 데이터와 수요 지표를 근거로 추가 확인 필요",
            "raw_text": "데이터 방향성 근거 " * 20,
        }
        report_tasks.extract_text = lambda pdf_bytes: "데이터 방향성 근거 소스 간 일치도 " * 300

        collect_result = await ReportCollectTaskHandler(connection=conn, settings=None)(
            {
                "stock_id": 1,
                "task_context": {
                    "stock_code": "005930",
                    "date_start": "2026-06-24",
                    "date_end": "2026-06-24",
                    "max_pages": 1,
                },
            }
        )
        process_result = await ReportProcessTaskHandler(
            connection=conn, settings=None, storage=storage
        )(conn.task("process_report"))
        normalize_result = await ReportNormalizeTaskHandler(connection=conn)(
            conn.task("normalize_report")
        )
        embed_result = await ReportEmbedTaskHandler(
            connection=conn, settings=None, storage=storage
        )(conn.task("embed_report"))
        analyze_result = await ReportAnalyzeTaskHandler(
            connection=conn, settings=None, analysis_agent=agent
        )(conn.task("analyze_report"))

        self.assertEqual(collect_result["collected"], 1)
        self.assertEqual(process_result["status"], "success")
        self.assertEqual(normalize_result["normalized_count"], 1)
        self.assertEqual(embed_result["status"], "success")
        self.assertEqual(analyze_result["analysis_result_id"], 100)
        self.assertEqual(
            conn.task_types(),
            ["process_report", "normalize_report", "embed_report", "analyze_report", "ml_infer"],
        )

        raw_document_id = next(iter(conn.raw_documents))
        signal_event_id = normalize_result["signal_event_ids"][0]
        analysis_result_id = analyze_result["analysis_result_id"]
        ml_task = conn.task("ml_infer")
        ml_context = json.loads(ml_task["task_context"])

        self.assertIn(raw_document_id, conn.raw_documents)
        self.assertEqual(conn.report_raw_details[raw_document_id]["parsing_status"], "success")
        self.assertTrue(conn.source_documents)
        self.assertTrue(conn.signal_events)
        self.assertTrue(conn.report_chunks)
        self.assertTrue(conn.analysis_results)
        self.assertTrue(conn.agent_results)
        self.assertEqual(conn.report_chunks[0]["embedding"].count(",") + 1, EMBEDDING_DIM)
        self.assertEqual(conn.task("normalize_report")["source_raw_ids"], [raw_document_id])
        self.assertEqual(conn.task("embed_report")["source_signal_event_ids"], [signal_event_id])
        self.assertEqual(conn.task("analyze_report")["source_signal_event_ids"], [signal_event_id])
        self.assertEqual(
            conn.analysis_results[analysis_result_id]["source_signal_event_ids"],
            [signal_event_id],
        )
        self.assertEqual(
            next(iter(conn.agent_results.values()))["source_signal_event_ids"],
            [signal_event_id],
        )
        self.assertEqual(ml_task["source_analysis_result_ids"], [analysis_result_id])
        self.assertEqual(
            ml_context["aggregate_ctx"]["source_analysis_result_ids"],
            [analysis_result_id],
        )
        self.assertEqual(ml_context["aggregate_ctx"]["signal_date"], date(2026, 6, 24).isoformat())
        self.assertEqual(agent.received.events[0]["id"], signal_event_id)
        self.assertEqual(agent.received.context["report_quant"]["report_count"], 1)

    async def test_report_pipeline_runs_through_queue_runner_to_aggregate_enqueue(self):
        conn = FakeReportPipelineConnection()
        storage = FakeReportStorage()
        agent = FakeReportAgent()

        report_tasks.collect_stock = lambda **kwargs: [
            {
                "firm": "Test Securities",
                "title": "005930 데이터 방향성 점검",
                "date": "2026.06.24",
                "pdf_direct_url": "https://example.com/report.pdf",
                "report_type": "cr",
            }
        ]
        report_tasks.download_and_upload = lambda url, key, passed_storage: bool(
            passed_storage.upload_pdf(b"%PDF-fake", key)
        )
        report_tasks.process_from_s3 = lambda key, passed_storage, *, settings=None: {
            "opinion": "neutral",
            "target_price": 90000,
            "key_rationale": "실적 데이터와 수요 지표를 근거로 추가 확인 필요",
            "raw_text": "데이터 방향성 근거 " * 20,
        }
        report_tasks.extract_text = lambda pdf_bytes: "데이터 방향성 근거 소스 간 일치도 " * 300

        conn.seed_task(
            task_type=COLLECT_REPORT,
            stock_id=1,
            task_context={
                "stock_code": "005930",
                "date_start": "2026-06-24",
                "date_end": "2026-06-24",
                "max_pages": 1,
            },
        )
        runner = QueueTaskRunner(
            conn,
            {
                COLLECT_REPORT: ReportCollectTaskHandler(connection=conn, settings=None),
                PROCESS_REPORT: ReportProcessTaskHandler(
                    connection=conn, settings=None, storage=storage
                ),
                NORMALIZE_REPORT: ReportNormalizeTaskHandler(connection=conn),
                EMBED_REPORT: ReportEmbedTaskHandler(connection=conn, settings=None, storage=storage),
                ANALYZE_REPORT: ReportAnalyzeTaskHandler(
                    connection=conn, settings=None, analysis_agent=agent
                ),
                ML_INFER: MlInferTaskHandler(conn),
            },
        )

        for task_type in (
            COLLECT_REPORT,
            PROCESS_REPORT,
            NORMALIZE_REPORT,
            EMBED_REPORT,
            ANALYZE_REPORT,
            ML_INFER,
        ):
            result = await runner.run_task(task_type)
            self.assertEqual(result["status"], "success", task_type)

        aggregate_task = conn.task(AGGREGATE_SIGNAL)
        self.assertEqual(aggregate_task["source_analysis_result_ids"], [100])
        self.assertEqual(json.loads(aggregate_task["task_context"])["signal_date"], "2026-06-24")
        self.assertEqual(
            [
                task["task_type"]
                for task in conn.processing_queue
                if task["status"] == "success"
            ],
            [COLLECT_REPORT, PROCESS_REPORT, NORMALIZE_REPORT, EMBED_REPORT, ANALYZE_REPORT, ML_INFER],
        )


if __name__ == "__main__":
    unittest.main()
