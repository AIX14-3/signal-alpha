import unittest

from app.orchestrator.dart.tasks import DartEventStudyBackfillTaskHandler


class FakeEventStudyBuilder:
    def __init__(self, count=3):
        self.count = count
        self.calls = []

    async def run(self, connection, *, event_filter_sql="", event_filter_args=()):
        self.calls.append(
            {
                "connection": connection,
                "event_filter_sql": event_filter_sql,
                "event_filter_args": event_filter_args,
            }
        )
        return self.count


class DartEventStudyBackfillTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_backfills_requested_dart_signal_events(self):
        builder = FakeEventStudyBuilder(count=2)
        handler = DartEventStudyBackfillTaskHandler(
            connection="conn",
            event_study_builder=builder,
        )

        result = await handler(
            {
                "stock_id": 7,
                "source_signal_event_ids": [101, 102],
                "task_context": {"stock_code": "005930"},
            }
        )

        self.assertEqual(result["backfilled_count"], 2)
        self.assertEqual(result["source_signal_event_ids"], [101, 102])
        self.assertEqual(builder.calls[0]["connection"], "conn")
        self.assertIn("source_type = $1", builder.calls[0]["event_filter_sql"])
        self.assertIn("id = ANY($2::BIGINT[])", builder.calls[0]["event_filter_sql"])
        self.assertEqual(builder.calls[0]["event_filter_args"], ("DART", [101, 102]))

    async def test_backfills_stock_dart_events_when_event_ids_are_missing(self):
        builder = FakeEventStudyBuilder(count=5)
        handler = DartEventStudyBackfillTaskHandler(
            connection="conn",
            event_study_builder=builder,
        )

        result = await handler({"stock_id": 7, "task_context": {"stock_code": "005930"}})

        self.assertEqual(result["backfilled_count"], 5)
        self.assertEqual(result["source_signal_event_ids"], [])
        self.assertIn("source_type = $1", builder.calls[0]["event_filter_sql"])
        self.assertIn("stock_id = $2", builder.calls[0]["event_filter_sql"])
        self.assertEqual(builder.calls[0]["event_filter_args"], ("DART", 7))


if __name__ == "__main__":
    unittest.main()
