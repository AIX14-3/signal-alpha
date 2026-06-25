import json
import unittest

from signal_alpha_data_access.repositories.collection import CollectionRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"raw_document_id": args[0], "stock_id": args[1]}

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return []


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

    async def test_upsert_report_valuation_fact_serializes_peer_group_for_jsonb(self):
        connection = FakeConnection()
        repository = CollectionRepository(connection)

        await repository.upsert_report_valuation_fact(
            raw_document_id=1,
            stock_id=10,
            ticker="005930",
            broker="Test Securities",
            analyst="Analyst A",
            publish_date="2026-06-24",
            target_price=120000,
            forward_eps_est=8000,
            eps_fy=2026,
            methodology="PER",
            applied_multiple=15.0,
            implied_multiple=15.0,
            peer_group=["SK Hynix", "Micron"],
            category_tag="memory",
            rerating_thesis="AI memory valuation context",
            extraction_source="rules",
            needs_review=False,
        )

        call = connection.calls[0]
        self.assertEqual(call[0], "fetchrow")
        self.assertIn("INSERT INTO report_valuation_facts", call[1])
        self.assertIn("ON CONFLICT (raw_document_id)", call[1])
        self.assertIsInstance(call[2][12], str)
        self.assertEqual(json.loads(call[2][12]), ["SK Hynix", "Micron"])

    async def test_list_report_valuation_facts_filters_by_stock_and_limit(self):
        connection = FakeConnection()
        repository = CollectionRepository(connection)

        await repository.list_report_valuation_facts(stock_id=10, limit=5)

        call = connection.calls[0]
        self.assertEqual(call[0], "fetch")
        self.assertIn("FROM report_valuation_facts", call[1])
        self.assertIn("WHERE stock_id = $1", call[1])
        self.assertIn("LIMIT $2", call[1])
        self.assertEqual(call[2], (10, 5))
