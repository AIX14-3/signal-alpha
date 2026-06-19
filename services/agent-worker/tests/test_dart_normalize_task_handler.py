import json
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.dart.tasks import DartAnalyzeTaskHandler, DartNormalizeTaskHandler


class FakeConnection:
    def __init__(self, rows=None):
        self.calls = []
        self.next_id = 400
        self.rows = rows

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        rows = self.rows or [
            {
                "raw_document_id": 10,
                "stock_id": 1,
                "source_type": "DART",
                "source_name": "OpenDART",
                "title": "분기보고서",
                "source_url": "https://dart.example/receipt",
                "published_at": datetime(2026, 6, 8),
                "collected_at": datetime(2026, 6, 8),
                "receipt_no": "202606080001",
                "corp_code": "00126380",
                "report_name": "분기보고서",
                "disclosure_type": "quarter_report",
                "is_correction": False,
                "original_receipt_no": None,
                "extra_payload": {
                    "document_text": "Original DART body for analysis. 매출액 77,781억원, 영업이익 6,606억원, 당기순이익 5,745억원.",
                },
            }
        ]
        if "signal_events.id = ANY" in sql and args:
            requested_ids = set(args[0])
            return [row for row in rows if row.get("id") in requested_ids]
        return rows

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        self.next_id += 1
        return {"id": self.next_id}

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "FROM processing_queue" in sql:
            return None
        self.next_id += 1
        return self.next_id


class FakeLlmAnalyzer:
    model = "test-llm"
    prompt_version = "dart-llm-v1"

    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    async def analyze(self, *, events, rule_result, stock_code):
        self.calls.append({"events": events, "rule_result": rule_result, "stock_code": stock_code})
        if self.fail:
            raise RuntimeError("LLM timeout")

        from app.analyzers.dart.llm import DartLlmAnalysis

        return DartLlmAnalysis(
            direction="positive",
            score=0.73,
            summary="LLM reviewed the disclosure and found improving performance.",
            key_facts=["Revenue improved", "Operating profit improved"],
            risk_flags=[],
            needs_review=False,
            confidence=82,
        )


class DartNormalizeTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_handler_normalizes_dart_raw_documents(self):
        connection = FakeConnection()
        handler = DartNormalizeTaskHandler(connection)

        result = await handler(
            {
                "id": 20,
                "stock_id": 1,
                "source_raw_ids": [10],
                "task_context": {"stock_code": "005930"},
            }
        )

        self.assertEqual(result["normalized_count"], 1)
        self.assertTrue(any("INSERT INTO source_documents" in call[1] for call in connection.calls))
        signal_event_call = next(call for call in connection.calls if "INSERT INTO signal_events" in call[1])
        self.assertIsInstance(signal_event_call[2][5], date)
        self.assertIn("Original DART body for analysis.", signal_event_call[2][10])
        self.assertTrue(any("INSERT INTO signal_metrics" in call[1] for call in connection.calls))
        metric_calls = [call for call in connection.calls if "INSERT INTO signal_metrics" in call[1]]
        metric_names = [call[2][1] for call in metric_calls]
        self.assertIn("dart_revenue", metric_names)
        revenue_call = next(call for call in metric_calls if call[2][1] == "dart_revenue")
        self.assertEqual(revenue_call[2][2:4], (7778100, "KRW_million"))
        self.assertTrue(any("INSERT INTO validation_logs" in call[1] for call in connection.calls))
        self.assertTrue(any("INSERT INTO processing_queue" in call[1] for call in connection.calls))
        self.assertIsNotNone(result["analysis_task_id"])
        enqueue_call = next(call for call in connection.calls if "INSERT INTO processing_queue" in call[1])
        self.assertEqual(enqueue_call[2][4], [402])
        self.assertEqual(
            enqueue_call[2][6],
            '{"stock_code": "005930", "source_type": "DART", "run_key": "DART_EVENT_402"}',
        )

    async def test_handler_accepts_json_string_raw_ids(self):
        connection = FakeConnection()
        handler = DartNormalizeTaskHandler(connection)

        result = await handler(
            {
                "id": 20,
                "stock_id": 1,
                "source_raw_ids": "{10}",
                "task_context": '{"stock_code": "005930"}',
            }
        )

        self.assertEqual(result["normalized_count"], 1)
        self.assertEqual(connection.calls[0][2], ([10],))

    async def test_handler_normalizes_correction_flag_as_correction_event(self):
        connection = FakeConnection(
            rows=[
                {
                    "raw_document_id": 10,
                    "stock_id": 1,
                    "source_type": "DART",
                    "source_name": "OpenDART",
                    "title": "Quarterly report",
                    "source_url": "https://dart.example/receipt",
                    "published_at": datetime(2026, 6, 8),
                    "collected_at": datetime(2026, 6, 8),
                    "receipt_no": "202606080002",
                    "corp_code": "00126380",
                    "report_name": "Quarterly report",
                    "disclosure_type": "quarter_report",
                    "is_correction": True,
                    "original_receipt_no": "202606080001",
                    "extra_payload": {},
                }
            ]
        )
        handler = DartNormalizeTaskHandler(connection)

        await handler(
            {
                "id": 20,
                "stock_id": 1,
                "source_raw_ids": [10],
                "task_context": {"stock_code": "005930"},
            }
        )

        signal_event_call = next(call for call in connection.calls if "INSERT INTO signal_events" in call[1])
        self.assertEqual(signal_event_call[2][4], "correction")
        self.assertTrue(signal_event_call[2][12])


class DartAnalyzeTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_handler_persists_dart_analysis_and_agent_result(self):
        connection = FakeConnection(
            rows=[
                {
                    "id": 501,
                    "stock_id": 1,
                    "source_document_id": 401,
                    "event_hash": "hash",
                    "source_type": "DART",
                    "event_type": "periodic_report",
                    "event_date": date(2026, 6, 8),
                    "signal_direction": "neutral",
                    "impact_level": "medium",
                    "title": "Quarterly report",
                    "summary": "DART disclosure: Quarterly report",
                    "evidence_text": "Quarterly report body",
                    "evidence_url": "https://dart.example/receipt",
                    "needs_review": False,
                    "source_name": "OpenDART",
                    "source_url": "https://dart.example/receipt",
                    "published_at": datetime(2026, 6, 8),
                    "reliability_level": "high",
                    "is_official": True,
                }
            ]
        )
        handler = DartAnalyzeTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_signal_event_ids": [501],
                "task_context": {"stock_code": "005930"},
            }
        )

        self.assertEqual(result["analyzed_count"], 1)
        self.assertEqual(result["direction"], "neutral")
        self.assertEqual(result["score"], 0.0)
        self.assertIsNotNone(result["aggregate_task_id"])
        self.assertTrue(any("INSERT INTO analysis_results" in call[1] for call in connection.calls))
        analysis_call = next(call for call in connection.calls if "INSERT INTO analysis_results" in call[1])
        self.assertEqual(analysis_call[2][5], 50.0)
        agent_call = next(call for call in connection.calls if "INSERT INTO agent_results" in call[1])
        self.assertEqual(agent_call[2][2], "D-1")
        self.assertEqual(agent_call[2][4], 50.0)
        self.assertEqual(agent_call[2][5], "neutral")
        self.assertEqual(agent_call[2][10], "dart-rules-v1")
        method_detail = json.loads(agent_call[2][6])
        self.assertEqual(method_detail["source_score"], 0.0)
        self.assertEqual(method_detail["graph"], "dart_analysis_v1")
        self.assertEqual(method_detail["graph_nodes"], ["validate_input", "analyze", "validate_output"])
        queue_call = next(call for call in connection.calls if "INSERT INTO processing_queue" in call[1])
        self.assertEqual(queue_call[2][1], "aggregate_signal")
        self.assertEqual(queue_call[2][5], [401])
        task_context = json.loads(queue_call[2][6])
        self.assertEqual(task_context["stock_code"], "005930")
        self.assertEqual(task_context["signal_date"], "2026-06-08")
        self.assertEqual(task_context["run_key"], "AGGREGATED")

    async def test_handler_uses_llm_for_high_impact_dart_event(self):
        connection = FakeConnection(
            rows=[
                {
                    "id": 501,
                    "stock_id": 1,
                    "source_document_id": 401,
                    "event_hash": "hash",
                    "source_type": "DART",
                    "event_type": "quarter_report",
                    "event_date": date(2026, 5, 15),
                    "signal_direction": "neutral",
                    "impact_level": "medium",
                    "title": "Quarterly report",
                    "summary": "DART disclosure: Quarterly report",
                    "evidence_text": "Revenue improved.",
                    "evidence_url": "https://dart.example/receipt",
                    "needs_review": False,
                    "source_name": "OpenDART",
                    "source_url": "https://dart.example/receipt",
                    "published_at": datetime(2026, 5, 15),
                    "reliability_level": "high",
                    "is_official": True,
                }
            ]
        )
        llm_analyzer = FakeLlmAnalyzer()
        handler = DartAnalyzeTaskHandler(connection, llm_analyzer=llm_analyzer)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_signal_event_ids": [501],
                "task_context": {"stock_code": "005930", "use_llm": True},
            }
        )

        self.assertEqual(result["direction"], "positive")
        self.assertEqual(result["score"], 0.73)
        self.assertEqual(result["analysis_source"], "llm")
        self.assertEqual(len(llm_analyzer.calls), 1)
        analysis_call = next(call for call in connection.calls if "INSERT INTO analysis_results" in call[1])
        self.assertEqual(analysis_call[2][5], 86.5)
        self.assertEqual(analysis_call[2][11], "dart-llm-v1")
        agent_call = next(call for call in connection.calls if "INSERT INTO agent_results" in call[1])
        self.assertEqual(agent_call[2][4], 86.5)
        self.assertEqual(agent_call[2][5], "positive")
        self.assertEqual(agent_call[2][9], "test-llm")
        self.assertEqual(agent_call[2][10], "dart-llm-v1")
        method_detail = json.loads(agent_call[2][6])
        self.assertEqual(method_detail["source_score"], 0.73)
        self.assertEqual(method_detail["analysis_source"], "llm")
        self.assertEqual(method_detail["key_facts"], ["Revenue improved", "Operating profit improved"])

    async def test_handler_falls_back_to_rules_when_llm_fails(self):
        connection = FakeConnection(
            rows=[
                {
                    "id": 501,
                    "stock_id": 1,
                    "source_document_id": 401,
                    "event_hash": "hash",
                    "source_type": "DART",
                    "event_type": "quarter_report",
                    "event_date": date(2026, 5, 15),
                    "signal_direction": "neutral",
                    "impact_level": "medium",
                    "title": "Quarterly report",
                    "summary": "DART disclosure: Quarterly report",
                    "evidence_text": "Revenue was stable.",
                    "evidence_url": "https://dart.example/receipt",
                    "needs_review": False,
                    "source_name": "OpenDART",
                    "source_url": "https://dart.example/receipt",
                    "published_at": datetime(2026, 5, 15),
                    "reliability_level": "high",
                    "is_official": True,
                }
            ]
        )
        handler = DartAnalyzeTaskHandler(connection, llm_analyzer=FakeLlmAnalyzer(fail=True))

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_signal_event_ids": [501],
                "task_context": {"stock_code": "005930", "use_llm": True},
            }
        )

        self.assertEqual(result["direction"], "neutral")
        self.assertEqual(result["analysis_source"], "rules_fallback")
        analysis_call = next(call for call in connection.calls if "INSERT INTO analysis_results" in call[1])
        self.assertEqual(analysis_call[2][11], "dart-rules-v1")
        agent_call = next(call for call in connection.calls if "INSERT INTO agent_results" in call[1])
        self.assertEqual(agent_call[2][9], None)
        self.assertEqual(agent_call[2][10], "dart-rules-v1")
        method_detail = json.loads(agent_call[2][6])
        self.assertEqual(method_detail["analysis_source"], "rules_fallback")
        self.assertEqual(method_detail["llm_error"], "LLM timeout")

    async def test_handler_with_event_ids_analyzes_only_requested_dart_event(self):
        connection = FakeConnection(
            rows=[
                {
                    "id": 501,
                    "stock_id": 1,
                    "source_document_id": 401,
                    "event_hash": "hash-1",
                    "source_type": "DART",
                    "event_type": "insider_ownership",
                    "event_date": date(2026, 6, 8),
                    "signal_direction": "neutral",
                    "impact_level": "low",
                    "title": "Insider ownership",
                    "summary": "DART disclosure: Insider ownership",
                    "evidence_text": "Insider ownership body",
                    "evidence_url": "https://dart.example/501",
                    "needs_review": False,
                    "source_name": "OpenDART",
                    "source_url": "https://dart.example/501",
                    "published_at": datetime(2026, 6, 8),
                    "reliability_level": "high",
                    "is_official": True,
                },
                {
                    "id": 502,
                    "stock_id": 1,
                    "source_document_id": 402,
                    "event_hash": "hash-2",
                    "source_type": "DART",
                    "event_type": "dart_disclosure",
                    "event_date": date(2026, 6, 8),
                    "signal_direction": "unknown",
                    "impact_level": "low",
                    "title": "Major shareholder change",
                    "summary": "DART disclosure: Major shareholder change",
                    "evidence_text": "Major shareholder change body",
                    "evidence_url": "https://dart.example/502",
                    "needs_review": True,
                    "source_name": "OpenDART",
                    "source_url": "https://dart.example/502",
                    "published_at": datetime(2026, 6, 8),
                    "reliability_level": "high",
                    "is_official": True,
                },
            ]
        )
        handler = DartAnalyzeTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_signal_event_ids": [501],
                "task_context": {
                    "stock_code": "005930",
                    "source_type": "DART",
                    "run_key": "DART_EVENT_501",
                },
            }
        )

        self.assertEqual(result["analyzed_count"], 1)
        id_query = connection.calls[0]
        self.assertIn("signal_events.id = ANY($1::BIGINT[])", id_query[1])
        self.assertEqual(id_query[2], ([501],))
        analysis_call = next(call for call in connection.calls if "INSERT INTO analysis_results" in call[1])
        self.assertEqual(analysis_call[2][4], [501])
        self.assertEqual(analysis_call[2][2], date(2026, 6, 8))
        self.assertEqual(analysis_call[2][3], "DART_EVENT_501")

    async def test_handler_without_event_ids_skips_date_level_analysis(self):
        connection = FakeConnection(rows=[])
        handler = DartAnalyzeTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_signal_event_ids": None,
                "task_context": {
                    "stock_code": "005930",
                    "source_type": "DART",
                    "analysis_date": "2026-06-08",
                },
            }
        )

        self.assertEqual(result["analyzed_count"], 0)
        self.assertEqual(result["skipped_reason"], "source_signal_event_ids_required")
        self.assertFalse(any("INSERT INTO analysis_results" in call[1] for call in connection.calls))
