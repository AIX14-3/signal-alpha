import unittest
import json

from signal_alpha_data_access.repositories.raw_details import RawDetailRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"raw_document_id": args[0], "stock_id": args[1]}


class RawDetailRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_dart_detail_uses_raw_document_conflict(self):
        connection = FakeConnection()
        repository = RawDetailRepository(connection)

        await repository.upsert_dart_detail(
            raw_document_id=1,
            stock_id=10,
            receipt_no="202606080001",
            report_name="분기보고서",
            extra_payload={"corp": "삼성전자"},
        )

        self.assertIn("ON CONFLICT (raw_document_id)", connection.calls[0][1])
        self.assertIsInstance(connection.calls[0][2][10], str)
        self.assertEqual(json.loads(connection.calls[0][2][10])["corp"], "삼성전자")

    async def test_upsert_hiring_detail_uses_raw_document_conflict(self):
        connection = FakeConnection()
        repository = RawDetailRepository(connection)

        await repository.upsert_hiring_detail(raw_document_id=2, stock_id=10)

        self.assertIn("ON CONFLICT (raw_document_id)", connection.calls[0][1])

    async def test_upsert_patent_detail_uses_application_conflict(self):
        connection = FakeConnection()
        repository = RawDetailRepository(connection)

        await repository.upsert_patent_detail(
            raw_document_id=3,
            stock_id=10,
            application_no="10-2026-0001",
            patent_title="반도체 공정",
            application_date="2026-06-08",
            extra_payload={},
        )

        self.assertIn("ON CONFLICT (application_no)", connection.calls[0][1])

    async def test_upsert_datalab_detail_uses_datalab_unique_key(self):
        connection = FakeConnection()
        repository = RawDetailRepository(connection)

        await repository.upsert_datalab_detail(
            raw_document_id=4,
            stock_id=10,
            keyword="HBM",
            observed_date="2026-06-08",
            search_index=88.5,
        )

        self.assertIn("ON CONFLICT (stock_id, keyword, observed_date, period_type, device, gender, age_group)", connection.calls[0][1])
