import unittest
from datetime import date, datetime

from app.orchestrator.dart.tasks import DartOwnershipNormalizeTaskHandler
from app.orchestrator.queue.task_types import ANALYZE_DART, BACKFILL_DART_LABELS


class FakeOwnershipRepository:
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


def _row(**overrides):
    base = {
        "id": 77,
        "stock_id": 7,
        "corp_code": "00126380",
        "rcept_no": "20260101000001",
        "line_seq": 0,
        "report_date": date(2026, 1, 1),
        "holder_name": "홍길동",
        "holder_type": "executive",
        "shares": 10,
        "ratio": 0.01,
        "shares_delta": 10,
        "ratio_delta": 0.01,
        "report_reason": "대표이사",
        "fetched_at": datetime(2026, 1, 1, 9, 0),
    }
    base.update(overrides)
    return base


class DartOwnershipNormalizeTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_ownership_events_with_external_anchor_and_metrics(self):
        ownership = FakeOwnershipRepository([_row()])
        normalization = FakeNormalizationRepository()
        queue = FakeQueueRepository()
        handler = DartOwnershipNormalizeTaskHandler(
            connection=None,
            ownership_repository=ownership,
            normalization_repository=normalization,
            queue_repository=queue,
        )

        result = await handler({"stock_id": 7, "task_context": {"stock_code": "005930"}})

        self.assertEqual(result["normalized_count"], 1)
        self.assertEqual(result["signal_event_ids"], [102])
        self.assertEqual(result["label_backfill_task_id"], 901)
        self.assertEqual(result["analysis_task_id"], 902)
        self.assertEqual(ownership.calls, [{"stock_id": 7, "limit": 500}])

        doc = normalization.docs[0]
        self.assertEqual(doc["external_ref_type"], "dart_ownership_events")
        self.assertEqual(doc["external_ref_id"], 77)
        self.assertEqual(doc["stock_id"], 7)
        self.assertEqual(doc["source_type"], "DART")
        self.assertTrue(doc["is_official"])

        event = normalization.events[0]
        self.assertEqual(event["event_hash"], "dart-ownership:7:77")
        self.assertEqual(event["event_type"], "dart_ownership_change")
        self.assertEqual(event["event_date"], date(2026, 1, 1))
        self.assertEqual(event["signal_direction"], "positive")
        self.assertEqual(event["impact_level"], "medium")
        self.assertIn("지분변동", event["title"])
        self.assertIn("보유주식 변화", event["summary"])
        self.assertIn("rcpNo=20260101000001", event["evidence_url"])

        metric_names = {metric["metric_name"] for metric in normalization.metrics}
        self.assertEqual(
            metric_names,
            {
                "dart_ownership_shares",
                "dart_ownership_ratio",
                "dart_ownership_shares_delta",
                "dart_ownership_ratio_delta",
            },
        )
        self.assertEqual(normalization.validation_logs[0]["target_type"], "signal_event")
        self.assertEqual(normalization.validation_logs[0]["validation_type"], "source_trace")
        self.assertEqual(queue.calls[0]["stock_id"], 7)
        self.assertEqual(queue.calls[0]["task_type"], BACKFILL_DART_LABELS)
        self.assertEqual(queue.calls[0]["source_signal_event_ids"], [102])
        self.assertEqual(queue.calls[0]["task_context"], {"stock_code": "005930", "source_type": "DART"})
        self.assertTrue(queue.calls[0]["dedupe"])
        self.assertEqual(queue.calls[1]["stock_id"], 7)
        self.assertEqual(queue.calls[1]["task_type"], ANALYZE_DART)
        self.assertEqual(queue.calls[1]["source_signal_event_ids"], [102])
        self.assertEqual(
            queue.calls[1]["task_context"],
            {"stock_code": "005930", "source_type": "DART", "run_key": "DART_OWNERSHIP_102"},
        )
        self.assertTrue(queue.calls[1]["dedupe"])

    async def test_negative_delta_becomes_negative_direction(self):
        normalization = FakeNormalizationRepository()
        handler = DartOwnershipNormalizeTaskHandler(
            connection=None,
            ownership_repository=FakeOwnershipRepository([_row(shares_delta=-10, ratio_delta=-0.01)]),
            normalization_repository=normalization,
            queue_repository=FakeQueueRepository(),
        )

        await handler({"stock_id": 7, "task_context": {"stock_code": "005930"}})

        self.assertEqual(normalization.events[0]["signal_direction"], "negative")


if __name__ == "__main__":
    unittest.main()
