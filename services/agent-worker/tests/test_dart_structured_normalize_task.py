import unittest
from datetime import date, datetime

from app.orchestrator.dart.tasks import (
    DartEmployeeNormalizeTaskHandler,
    DartFinancialsNormalizeTaskHandler,
)
from app.orchestrator.queue.task_types import ANALYZE_DART


class FakeStructuredRepository:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def list_for_normalization(self, *, stock_id, limit=500):
        self.calls.append({"stock_id": stock_id, "limit": limit})
        return self.rows


class FakeNormalizationRepository:
    def __init__(self):
        self.docs = []
        self.events = []
        self.metrics = []
        self.validation_logs = []
        self.next_id = 100

    async def upsert_external_source_document(self, **kwargs):
        self.docs.append(kwargs)
        self.next_id += 1
        return {"id": self.next_id}

    async def upsert_signal_event(self, **kwargs):
        self.events.append(kwargs)
        self.next_id += 1
        return {"id": self.next_id}

    async def upsert_signal_metric(self, **kwargs):
        self.metrics.append(kwargs)
        return {"id": len(self.metrics)}

    async def record_validation_log(self, **kwargs):
        self.validation_logs.append(kwargs)
        return len(self.validation_logs)


class FakeQueueRepository:
    def __init__(self):
        self.calls = []

    async def enqueue(self, **kwargs):
        self.calls.append(kwargs)
        return 900 + len(self.calls)


def _financial_row(**overrides):
    base = {
        "id": 11,
        "stock_id": 7,
        "corp_code": "00126380",
        "rcept_no": "20260315000001",
        "bsns_year": 2025,
        "reprt_code": "11011",
        "fs_div": "CFS",
        "sj_div": "BS",
        "account_id": "ifrs-full_Assets",
        "account_nm": "Assets",
        "amount_krw": 100_000,
        "amount_raw": "100,000",
        "currency": "KRW",
        "period_label": "2025FY",
        "fiscal_period": "annual",
        "fetched_at": datetime(2026, 3, 15, 9, 0),
    }
    base.update(overrides)
    return base


def _employee_row(**overrides):
    base = {
        "id": 21,
        "stock_id": 7,
        "corp_code": "00126380",
        "rcept_no": "20260315000002",
        "bsns_year": 2025,
        "reprt_code": "11011",
        "line_seq": 0,
        "segment": "semiconductor",
        "sex": "M",
        "headcount": 10,
        "regular_count": 8,
        "contract_count": 2,
        "avg_tenure_years": 5.5,
        "avg_salary_krw": 70_000_000,
        "salary_total_krw": 700_000_000,
        "fetched_at": datetime(2026, 3, 15, 9, 0),
    }
    base.update(overrides)
    return base


class DartStructuredNormalizeTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_financials_normalize_promotes_group_to_dart_event_and_metrics(self):
        repository = FakeStructuredRepository(
            [
                _financial_row(),
                _financial_row(
                    id=12,
                    sj_div="IS",
                    account_id="ifrs-full_Revenue",
                    account_nm="Revenue",
                    amount_krw=50_000,
                ),
            ]
        )
        normalization = FakeNormalizationRepository()
        queue = FakeQueueRepository()
        handler = DartFinancialsNormalizeTaskHandler(
            connection=None,
            financials_repository=repository,
            normalization_repository=normalization,
            queue_repository=queue,
        )

        result = await handler({"stock_id": 7, "task_context": {"stock_code": "005930"}})

        self.assertEqual(result["normalized_count"], 1)
        self.assertEqual(result["signal_event_ids"], [102])
        self.assertEqual(result["analysis_task_id"], 901)
        self.assertEqual(repository.calls, [{"stock_id": 7, "limit": 500}])

        doc = normalization.docs[0]
        self.assertEqual(doc["external_ref_type"], "dart_financial_facts")
        self.assertEqual(doc["external_ref_id"], 11)
        self.assertEqual(doc["source_type"], "DART")
        self.assertEqual(doc["source_name"], "OpenDART Financials")
        self.assertTrue(doc["is_official"])

        event = normalization.events[0]
        self.assertEqual(event["event_hash"], "dart-financial:7:20260315000001:2025:11011:CFS")
        self.assertEqual(event["event_type"], "dart_financial_snapshot")
        self.assertEqual(event["event_date"], date(2026, 3, 15))
        self.assertEqual(event["signal_direction"], "neutral")
        self.assertEqual(event["impact_level"], "medium")
        self.assertIn("2025FY", event["title"])
        self.assertIn("2 metrics", event["summary"])
        self.assertIn("rcpNo=20260315000001", event["evidence_url"])

        metric_names = {metric["metric_name"] for metric in normalization.metrics}
        self.assertEqual(
            metric_names,
            {
                "dart_financial_ifrs_full_assets",
                "dart_financial_ifrs_full_revenue",
            },
        )
        self.assertEqual(queue.calls[0]["task_type"], ANALYZE_DART)
        self.assertEqual(queue.calls[0]["source_signal_event_ids"], [102])
        self.assertEqual(
            queue.calls[0]["task_context"],
            {"stock_code": "005930", "source_type": "DART", "run_key": "DART_FINANCIALS_102"},
        )
        self.assertEqual(normalization.validation_logs[0]["validation_type"], "source_trace")

    async def test_employee_normalize_promotes_group_to_dart_event_and_metrics(self):
        repository = FakeStructuredRepository([_employee_row()])
        normalization = FakeNormalizationRepository()
        queue = FakeQueueRepository()
        handler = DartEmployeeNormalizeTaskHandler(
            connection=None,
            employee_repository=repository,
            normalization_repository=normalization,
            queue_repository=queue,
        )

        result = await handler({"stock_id": 7, "task_context": {"stock_code": "005930"}})

        self.assertEqual(result["normalized_count"], 1)
        self.assertEqual(result["signal_event_ids"], [102])
        self.assertEqual(result["analysis_task_id"], 901)
        self.assertEqual(repository.calls, [{"stock_id": 7, "limit": 500}])

        doc = normalization.docs[0]
        self.assertEqual(doc["external_ref_type"], "dart_employee_stats")
        self.assertEqual(doc["external_ref_id"], 21)
        self.assertEqual(doc["source_name"], "OpenDART Employee")

        event = normalization.events[0]
        self.assertEqual(event["event_hash"], "dart-employee:7:20260315000002:2025:11011")
        self.assertEqual(event["event_type"], "dart_employee_snapshot")
        self.assertEqual(event["event_date"], date(2026, 3, 15))
        self.assertEqual(event["signal_direction"], "neutral")
        self.assertEqual(event["impact_level"], "low")
        self.assertIn("2025", event["title"])

        metric_names = {metric["metric_name"] for metric in normalization.metrics}
        self.assertEqual(
            metric_names,
            {
                "dart_employee_semiconductor_m_0_headcount",
                "dart_employee_semiconductor_m_0_regular_count",
                "dart_employee_semiconductor_m_0_contract_count",
                "dart_employee_semiconductor_m_0_avg_tenure_years",
                "dart_employee_semiconductor_m_0_avg_salary_krw",
                "dart_employee_semiconductor_m_0_salary_total_krw",
            },
        )
        self.assertEqual(queue.calls[0]["task_type"], ANALYZE_DART)
        self.assertEqual(
            queue.calls[0]["task_context"],
            {"stock_code": "005930", "source_type": "DART", "run_key": "DART_EMPLOYEE_102"},
        )


if __name__ == "__main__":
    unittest.main()
