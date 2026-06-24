import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.aggregation.tasks import AggregateSignalTaskHandler
from app.orchestrator.queue.handlers import build_task_handlers
from app.orchestrator.queue.task_types import AGGREGATE_SIGNAL


class FakeConnection:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or []
        self.next_id = 700

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.rows

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        self.next_id += 1
        return {"id": self.next_id}

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        self.next_id += 1
        return self.next_id


def dart_agent_row(
    *,
    analysis_result_id=100,
    agent_result_id=200,
    direction="neutral",
    source_score=0.0,
    method_score=50.0,
    data_status="ok",
    needs_review=False,
    source="DART",
):
    return {
        "analysis_result_id": analysis_result_id,
        "stock_id": 1,
        "analysis_date": date(2026, 6, 19),
        "analysis_run_key": "DART_EVENT_501",
        "analysis_mode": "dart_only",
        "analysis_version": "dart-rules-v1",
        "analysis_source_signal_event_ids": [501],
        "agent_result_id": agent_result_id,
        "debate_method": "D-1",
        "agent_source_signal_event_ids": [501],
        "method_score": method_score,
        "method_signal": direction,
        "method_detail": {
            "source": source,
            "source_score": source_score,
            "data_status": data_status,
            "summary": "DART disclosures show a neutral information direction.",
            "risk_flags": [],
            "needs_review": needs_review,
            "events": [{"id": 501, "title": "Quarterly report"}],
        },
        "reliability_score": 90,
        "evidence_quality": 100,
        "llm_model": None,
        "prompt_ver": "dart-rules-v1",
    }


class AggregateSignalTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_handler_publishes_dart_single_source_final_signal_with_caution(self):
        connection = FakeConnection(rows=[dart_agent_row()])
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )

        self.assertEqual(result["final_signal_id"], 702)
        self.assertEqual(result["signal"], "neutral")
        self.assertEqual(result["final_score"], 50.0)
        self.assertEqual(result["source_agreement"], "LOW")
        self.assertEqual(result["consensus_score"], 50.0)
        self.assertEqual(result["warning_level"], "CAUTION")
        self.assertTrue(result["needs_review"])
        self.assertTrue(result["is_published"])

        analysis_call = next(call for call in connection.calls if "INSERT INTO analysis_results" in call[1])
        self.assertEqual(analysis_call[2][3], "AGGREGATED")
        self.assertEqual(analysis_call[2][5], 50.0)
        self.assertEqual(analysis_call[2][8], "full")
        self.assertEqual(analysis_call[2][11], "final-agg-v1")

        final_call = next(call for call in connection.calls if "INSERT INTO final_signals" in call[1])
        args = final_call[2]
        self.assertEqual(args[3], "AGGREGATED")
        self.assertEqual(args[4], "final-agg-v1")
        self.assertEqual(args[5], 50.0)
        self.assertEqual(args[6], 50.0)
        self.assertEqual(args[7], "neutral")
        self.assertEqual(args[8], "LOW")
        self.assertEqual(args[9], "CAUTION")
        self.assertTrue(args[15])
        self.assertTrue(args[17])
        breakdown = json.loads(args[10])
        self.assertEqual(breakdown["DART"]["score"], 0.0)
        self.assertEqual(breakdown["PRICE"]["data_status"], "missing")
        self.assertEqual(breakdown["REPORT"]["data_status"], "missing")
        self.assertEqual(breakdown["ALTERNATIVE"]["data_status"], "missing")

    async def test_positive_and_negative_sources_publish_mixed_caution_before_score_threshold(self):
        rows = [
            dart_agent_row(direction="positive", source_score=1.0, method_score=100.0),
            dart_agent_row(
                analysis_result_id=101,
                agent_result_id=201,
                direction="negative",
                source_score=-0.5,
                method_score=25.0,
                source="REPORT",
            ),
        ]
        connection = FakeConnection(rows=rows)
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100, 101],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )

        self.assertEqual(result["signal"], "mixed")
        self.assertEqual(result["final_score"], 62.5)
        self.assertEqual(result["warning_level"], "CAUTION")
        self.assertTrue(result["needs_review"])
        self.assertTrue(result["is_published"])

    async def test_handler_accepts_report_single_source_final_signal(self):
        connection = FakeConnection(
            rows=[
                dart_agent_row(
                    analysis_result_id=100,
                    agent_result_id=200,
                    direction="positive",
                    source_score=0.36,
                    method_score=68.0,
                    source="REPORT",
                )
            ]
        )
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-24"},
            }
        )

        self.assertEqual(result["signal"], "positive")
        self.assertEqual(result["final_score"], 68.0)
        self.assertEqual(result["source_agreement"], "LOW")
        self.assertEqual(result["warning_level"], "CAUTION")
        self.assertTrue(result["is_published"])
        final_call = next(call for call in connection.calls if "INSERT INTO final_signals" in call[1])
        breakdown = json.loads(final_call[2][10])
        self.assertEqual(breakdown["REPORT"]["analysis_result_id"], 100)
        self.assertEqual(breakdown["REPORT"]["score"], 0.36)
        self.assertEqual(breakdown["DART"]["data_status"], "missing")

    async def test_unknown_source_is_excluded_and_records_validation_log(self):
        row = dart_agent_row(source="")
        row["analysis_run_key"] = "BATCH"
        row["analysis_mode"] = "full"
        connection = FakeConnection(rows=[row])
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )

        self.assertEqual(result["warning_level"], "WARNING")
        self.assertFalse(result["is_published"])
        self.assertTrue(any("INSERT INTO validation_logs" in call[1] for call in connection.calls))
        final_call = next(call for call in connection.calls if "INSERT INTO final_signals" in call[1])
        breakdown = json.loads(final_call[2][10])
        self.assertEqual(breakdown["DART"]["data_status"], "missing")

    async def test_queue_handlers_registers_aggregate_signal(self):
        handlers = build_task_handlers(FakeConnection())

        self.assertIn(AGGREGATE_SIGNAL, handlers)


if __name__ == "__main__":
    unittest.main()
