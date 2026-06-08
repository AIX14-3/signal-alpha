import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.dart_tasks import DartCollectionTaskHandler


class FakeSettings:
    dart_api_key = "test-key"
    dart_page_size = 50


class FakeClient:
    async def list_disclosures(self, **kwargs):
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


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.next_id = 300

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
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
