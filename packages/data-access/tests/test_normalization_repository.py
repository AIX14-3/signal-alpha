import unittest

from signal_alpha_data_access.repositories.normalization import NormalizationRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 30, "event_hash": args[2] if len(args) > 2 else "event"}

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return 40


class NormalizationRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_source_document_uses_raw_document_conflict(self):
        connection = FakeConnection()
        repository = NormalizationRepository(connection)

        row = await repository.upsert_source_document(
            raw_document_id=20,
            stock_id=1,
            source_type="REPORT",
            source_name="증권사 리포트",
            title="리포트",
            source_url="https://example.com/report",
            published_at="2026-06-08T00:00:00+09:00",
            collected_at="2026-06-08T01:00:00+09:00",
        )

        self.assertEqual(row["id"], 30)
        self.assertIn("ON CONFLICT (raw_document_id)", connection.calls[0][1])

    async def test_upsert_signal_event_uses_event_hash_conflict(self):
        connection = FakeConnection()
        repository = NormalizationRepository(connection)

        row = await repository.upsert_signal_event(
            stock_id=1,
            source_document_id=30,
            event_hash="hash-1",
            source_type="REPORT",
            event_type="report_published",
            event_date="2026-06-08",
            signal_direction="neutral",
            impact_level="medium",
            title="리포트 발간",
        )

        self.assertEqual(row["event_hash"], "hash-1")
        self.assertIn("ON CONFLICT (event_hash)", connection.calls[0][1])

    async def test_upsert_signal_metric_uses_event_metric_conflict(self):
        connection = FakeConnection()
        repository = NormalizationRepository(connection)

        row = await repository.upsert_signal_metric(
            signal_event_id=30,
            metric_name="target_price",
            metric_value=95000,
            metric_unit="KRW",
        )

        self.assertEqual(row["id"], 30)
        self.assertIn("ON CONFLICT (signal_event_id, metric_name)", connection.calls[0][1])

    async def test_record_validation_log_requires_one_target_identifier(self):
        connection = FakeConnection()
        repository = NormalizationRepository(connection)

        with self.assertRaises(ValueError):
            await repository.record_validation_log(
                target_type="signal_event",
                validation_type="source_trace",
                passed=True,
            )
