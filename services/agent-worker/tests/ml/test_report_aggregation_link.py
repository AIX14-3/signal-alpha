from __future__ import annotations

import json
import unittest

from app.ml.inference import MlInferTaskHandler
from app.ml.meta_combine import MetaCombineTaskHandler


REPORT_AGGREGATE_CTX = {
    "stock_code": "005930",
    "signal_date": "2026-06-24",
    "run_key": "AGGREGATED",
    "aggregation_key": "AGGREGATED:1:2026-06-24:final-agg-v1",
    "source_analysis_result_ids": [100],
}


class FakeLinkConnection:
    def __init__(self, *, fetch_rows=None):
        self.fetch_rows = fetch_rows or []
        self.calls = []
        self.next_id = 700

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.fetch_rows

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        self.next_id += 1
        return {"id": self.next_id}

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "SELECT id" in sql:
            return None
        self.next_id += 1
        return self.next_id

    def aggregate_enqueue(self):
        return next(
            call for call in self.calls
            if call[0] == "fetchval" and "INSERT INTO processing_queue" in call[1]
        )


class ReportAggregationLinkTest(unittest.IsolatedAsyncioTestCase):
    async def test_ml_infer_without_ohlcv_enqueues_aggregate_with_report_analysis_ids(self):
        connection = FakeLinkConnection(fetch_rows=[])
        handler = MlInferTaskHandler(connection)

        result = await handler(
            {
                "stock_id": 1,
                "task_context": {
                    "stock_code": "005930",
                    "run_key": "ML",
                    "aggregate_ctx": REPORT_AGGREGATE_CTX,
                },
            }
        )

        self.assertEqual(result["skipped_reason"], "no_ohlcv")
        enqueue = connection.aggregate_enqueue()
        self.assertEqual(enqueue[2][1], "aggregate_signal")
        self.assertEqual(enqueue[2][5], [100])
        task_context = json.loads(enqueue[2][6])
        self.assertEqual(task_context["stock_code"], "005930")
        self.assertEqual(task_context["signal_date"], "2026-06-24")
        self.assertEqual(task_context["aggregation_key"], "AGGREGATED:1:2026-06-24:final-agg-v1")
        self.assertNotIn("source_analysis_result_ids", task_context)

    async def test_meta_combine_enqueues_aggregate_with_report_analysis_ids(self):
        connection = FakeLinkConnection(
            fetch_rows=[
                {"model_name": "ewma", "pred_value": 0.2, "gate_passed": True},
                {"model_name": "har_rv", "pred_value": 0.4, "gate_passed": True},
            ]
        )
        handler = MetaCombineTaskHandler(connection)

        result = await handler(
            {
                "stock_id": 1,
                "task_context": {
                    "stock_code": "005930",
                    "run_key": "ML",
                    "asof_date": "2026-06-24",
                    "horizon": 10,
                    "aggregate_ctx": REPORT_AGGREGATE_CTX,
                },
            }
        )

        self.assertEqual(result["aggregate_task_id"], 702)
        enqueue = connection.aggregate_enqueue()
        self.assertEqual(enqueue[2][1], "aggregate_signal")
        self.assertEqual(enqueue[2][5], [100])
        task_context = json.loads(enqueue[2][6])
        self.assertEqual(task_context["run_key"], "AGGREGATED")
        self.assertEqual(task_context["signal_date"], "2026-06-24")


if __name__ == "__main__":
    unittest.main()
