import json
import unittest

from signal_alpha_data_access.repositories.collection import CollectionRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"raw_document_id": args[0], "stock_id": args[1]}


class CollectionRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_report_detail_serializes_extra_payload_for_jsonb(self):
        connection = FakeConnection()
        repository = CollectionRepository(connection)

        await repository.upsert_report_detail(
            raw_document_id=1,
            stock_id=10,
            securities_firm="한국투자증권",
            publish_date="2025-08-05",
            extra_payload={"report_type": "company_report"},
        )

        self.assertIsInstance(connection.calls[0][2][17], str)
        self.assertEqual(
            json.loads(connection.calls[0][2][17]),
            {"report_type": "company_report"},
        )
