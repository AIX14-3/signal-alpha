"""MetaCombineTaskHandler: ml_inferences 읽어 결합 → meta_signals 적재 (fake conn)."""

from __future__ import annotations

import unittest

from app.ml.meta_combine import MetaCombineTaskHandler


class FakeConnection:
    """list_for_run(fetch) / upsert_meta_signal(fetchrow)만 흉내내는 가짜 연결."""

    def __init__(self, inference_rows):
        self._inference_rows = inference_rows
        self.upsert_args = None

    async def fetch(self, sql, *args):
        return self._inference_rows

    async def fetchrow(self, sql, *args):
        self.upsert_args = args
        return {"id": 1}


class MetaCombineTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_combines_successful_inferences_and_persists(self):
        rows = [
            {"model_name": "ewma", "pred_value": 0.2, "gate_passed": True},
            {"model_name": "har_rv", "pred_value": 0.4, "gate_passed": True},
            {"model_name": "garch", "pred_value": None, "gate_passed": True},  # failed → excluded
        ]
        connection = FakeConnection(rows)
        handler = MetaCombineTaskHandler(connection)

        result = await handler(
            {
                "stock_id": 10,
                "task_context": {"run_key": "ML", "asof_date": "2026-06-22", "horizon": 10},
            }
        )

        # no artifact in repo → equal fallback over the two successful models
        self.assertEqual(result["method"], "equal_fallback")
        self.assertEqual(result["combined_vol"], 0.3)
        self.assertEqual(result["model_count"], 2)
        # persisted: combined_vol arg position (5th) matches
        self.assertEqual(connection.upsert_args[4], 0.3)

    async def test_requires_asof_date(self):
        handler = MetaCombineTaskHandler(FakeConnection([]))
        result = await handler({"stock_id": 10, "task_context": {"run_key": "ML"}})
        self.assertEqual(result["skipped_reason"], "asof_date_required")

    async def test_empty_inferences_yield_empty_method(self):
        connection = FakeConnection([])
        handler = MetaCombineTaskHandler(connection)

        result = await handler(
            {
                "stock_id": 10,
                "task_context": {"run_key": "ML", "asof_date": "2026-06-22", "horizon": 10},
            }
        )

        self.assertEqual(result["method"], "empty")
        self.assertIsNone(result["combined_vol"])
        self.assertEqual(result["model_count"], 0)


if __name__ == "__main__":
    unittest.main()
