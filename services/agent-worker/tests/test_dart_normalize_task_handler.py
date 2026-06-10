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
        return self.rows or [
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
                "extra_payload": {"document_text": "Original DART body for analysis."},
            }
        ]

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
        self.assertEqual(signal_event_call[2][10], "Original DART body for analysis.")
        self.assertTrue(any("INSERT INTO signal_metrics" in call[1] for call in connection.calls))
        self.assertTrue(any("INSERT INTO validation_logs" in call[1] for call in connection.calls))
        self.assertTrue(any("INSERT INTO processing_queue" in call[1] for call in connection.calls))
        self.assertEqual(result["analysis_task_id"], 405)

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
        self.assertTrue(any("INSERT INTO analysis_results" in call[1] for call in connection.calls))
        agent_call = next(call for call in connection.calls if "INSERT INTO agent_results" in call[1])
        self.assertEqual(agent_call[2][2], "D-1")
        self.assertEqual(agent_call[2][5], "neutral")
        self.assertEqual(agent_call[2][10], "dart-rules-v1")
