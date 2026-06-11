import sys
import unittest
import warnings
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.dart import get_dart_task_handler_factory, get_database_pool
from app.main import app


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.claim_counts = {}

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [
            {
                "id": 10,
                "stock_id": 1,
                "stock_code": "005930",
                "stock_name": "Samsung Electronics",
                "analysis_date": date(2026, 6, 8),
                "run_key": "DART",
                "analysis_mode": "dart_only",
                "version": "1.0",
                "source_signal_event_ids": [501],
                "base_score": 62,
                "warning": None,
                "agent_results": [
                    {
                        "debate_method": "D-1",
                        "method_signal": "positive",
                    }
                ],
                "signal_events": [
                    {
                        "id": 501,
                        "title": "Quarterly report",
                    }
                ],
            }
        ]

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "WHERE id = $1" in sql:
            task_id, task_type = args
            return {
                "id": task_id,
                "stock_id": 1,
                "task_type": task_type,
                "task_context": {"stock_code": "005930"},
                "retry_count": 0,
                "max_retry_count": 0,
            }
        if "WITH next_task AS" in sql:
            task_type = args[0]
            self.claim_counts[task_type] = self.claim_counts.get(task_type, 0) + 1
            if self.claim_counts[task_type] > 1:
                return None
            return {
                "id": 100 + len(self.claim_counts),
                "stock_id": 1,
                "task_type": task_type,
                "task_context": {"stock_code": "005930"},
                "retry_count": 0,
                "max_retry_count": 0,
            }
        return {
            "deleted_score_history_count": 0,
            "deleted_final_signal_count": 0,
            "deleted_agent_result_count": 1,
            "deleted_analysis_result_count": 1,
            "deleted_raw_document_count": 2,
        }

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "DELETE FROM raw_documents" in sql:
            return 2
        if "DELETE FROM agent_results" in sql or "DELETE FROM analysis_results" in sql:
            return 1
        if "DELETE FROM" in sql:
            return 0
        return 77

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "OK"


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return FakeAcquire(self.connection)


class DartRouteTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_list_analysis_results_filters_by_stock_and_date(self):
        connection = FakeConnection()
        app.dependency_overrides[get_database_pool] = lambda: FakePool(connection)
        client = TestClient(app)

        response = client.get(
            "/internal/dart/analysis-results",
            params={"stock_code": "005930", "analysis_date": "2026-06-08"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["items"][0]["stock_code"], "005930")
        self.assertEqual(response.json()["items"][0]["analysis_date"], "2026-06-08")
        self.assertEqual(response.json()["items"][0]["agent_results"][0]["method_signal"], "positive")
        self.assertEqual(connection.calls[0][2], ("005930", date(2026, 6, 8), 20))

    def test_delete_test_data_returns_deleted_counts(self):
        connection = FakeConnection()
        app.dependency_overrides[get_database_pool] = lambda: FakePool(connection)
        client = TestClient(app)

        response = client.delete(
            "/internal/dart/test-data",
            params={"stock_code": "005930", "bgn_de": "2026-06-01", "end_de": "2026-06-30"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted_raw_document_count"], 2)
        self.assertEqual(connection.calls[0][2], ("005930", date(2026, 6, 1), date(2026, 6, 30)))

    def test_run_e2e_enqueues_and_drains_dart_tasks(self):
        async def collect_handler(task):
            return {"collected_count": 1, "queued_task_ids": [201]}

        async def normalize_handler(task):
            return {"normalized_count": 1, "analysis_task_ids": [301]}

        async def analyze_handler(task):
            return {"analysis_result_id": 10}

        connection = FakeConnection()
        app.dependency_overrides[get_database_pool] = lambda: FakePool(connection)
        app.dependency_overrides[get_dart_task_handler_factory] = lambda: lambda _: {
            "collect_dart": collect_handler,
            "normalize_dart": normalize_handler,
            "analyze_dart": analyze_handler,
        }
        client = TestClient(app)

        response = client.post(
            "/internal/dart/e2e/run",
            json={
                "stock_id": 1,
                "stock_code": "005930",
                "bgn_de": "2026-06-01",
                "end_de": "2026-06-08",
                "force_reprocess": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["collect_task_id"], 77)
        self.assertEqual(payload["collect"]["status"], "success")
        self.assertEqual(payload["normalize"][0]["status"], "success")
        self.assertEqual(payload["normalize"][0]["task_id"], 201)
        self.assertEqual(payload["analyze"][0]["status"], "success")
        self.assertEqual(payload["analyze"][0]["task_id"], 301)
        self.assertEqual(payload["analysis_results"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
