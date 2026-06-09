import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.dart_tasks import DartCollectionTaskHandler


class FakeSettings:
    dart_api_key = "test-key"
    dart_page_size = 50
    dart_fetch_documents = True


class FakeClient:
    def __init__(self):
        self.calls = []

    async def list_disclosures(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": "000",
            "list": [
                {
                    "corp_code": "00126380",
                    "corp_name": "삼성전자",
                    "stock_code": "005930",
                    "corp_cls": "Y",
                    "report_nm": "분기보고서",
                    "rcept_no": "202606080001",
                    "flr_nm": "삼성전자",
                    "rcept_dt": "20260608",
                    "rm": "",
                }
            ],
        }

    async def fetch_document(self, receipt_no):
        return {"text": "DART original document body", "files": [{"name": "document.xml", "text_length": 27}]}


class FakeConnection:
    def __init__(self, *, state_last_end_de="20260608"):
        self.calls = []
        self.next_id = 300
        self.state_last_end_de = state_last_end_de

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "FROM dart_collection_states" in sql:
            return {"last_end_de": self.state_last_end_de, "last_receipt_no": "202606080001"}
        if "FROM dart_corp_codes" in sql:
            return {"corp_code": "00126380", "corp_name": "삼성전자"}
        self.next_id += 1
        return {"id": self.next_id, "source_hash": "hash"}

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        self.next_id += 1
        return self.next_id

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "OK"

    async def executemany(self, sql, args):
        self.calls.append(("executemany", sql, tuple(args)))
        return "OK"


class DartCollectionTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_handler_collects_dart_and_persists_evidence(self):
        connection = FakeConnection()
        handler = DartCollectionTaskHandler(
            connection=connection,
            settings=FakeSettings(),
            client=FakeClient(),
        )

        result = await handler(
            {
                "id": 10,
                "stock_id": 1,
                "task_context": {
                    "stock_code": "005930",
                    "bgn_de": "20260601",
                    "end_de": "20260608",
                },
            }
        )

        self.assertEqual(result["collected_count"], 1)
        self.assertEqual(result["inserted_count"], 1)
        self.assertTrue(any("FROM dart_corp_codes" in call[1] for call in connection.calls))
        self.assertTrue(any("INSERT INTO dart_raw_details" in call[1] for call in connection.calls))
        self.assertTrue(any("INSERT INTO processing_queue" in call[1] for call in connection.calls))
        dart_detail_call = next(call for call in connection.calls if "INSERT INTO dart_raw_details" in call[1])
        self.assertIn("document_text", dart_detail_call[2][-1])

    async def test_handler_accepts_json_string_task_context(self):
        connection = FakeConnection()
        handler = DartCollectionTaskHandler(
            connection=connection,
            settings=FakeSettings(),
            client=FakeClient(),
        )

        result = await handler(
            {
                "id": 10,
                "stock_id": 1,
                "task_context": '{"stock_code": "005930", "bgn_de": "20260601", "end_de": "20260608"}',
            }
        )

        self.assertEqual(result["collected_count"], 1)

    async def test_handler_uses_collection_state_when_start_date_is_missing(self):
        connection = FakeConnection()
        client = FakeClient()
        handler = DartCollectionTaskHandler(
            connection=connection,
            settings=FakeSettings(),
            client=client,
        )

        result = await handler(
            {
                "id": 10,
                "stock_id": 1,
                "task_context": {
                    "stock_code": "005930",
                    "end_de": "20260610",
                },
            }
        )

        self.assertEqual(result["collected_count"], 1)
        self.assertEqual(client.calls[0]["bgn_de"], "20260609")
        state_call = next(call for call in connection.calls if "INSERT INTO dart_collection_states" in call[1])
        self.assertEqual(state_call[2][0:2], (1, "005930"))
        self.assertEqual(state_call[2][2], date(2026, 6, 9))
        self.assertEqual(state_call[2][3], date(2026, 6, 10))

    async def test_handler_skips_when_collection_state_already_covers_end_date(self):
        connection = FakeConnection(state_last_end_de="20260609")
        client = FakeClient()
        handler = DartCollectionTaskHandler(
            connection=connection,
            settings=FakeSettings(),
            client=client,
        )

        result = await handler(
            {
                "id": 10,
                "stock_id": 1,
                "task_context": {
                    "stock_code": "005930",
                    "end_de": "20260609",
                },
            }
        )

        self.assertEqual(result["collected_count"], 0)
        self.assertEqual(result["inserted_count"], 0)
        self.assertEqual(result["skipped_reason"], "dart_collection_up_to_date")
        self.assertEqual(client.calls, [])
        self.assertFalse(any("INSERT INTO dart_collection_states" in call[1] for call in connection.calls))
