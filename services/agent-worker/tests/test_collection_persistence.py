import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.persistence import CollectionPersistence
from app.schemas.evidence import RawEvidence


class FakeConnection:
    def __init__(self, *, raw_inserted=True):
        self.calls = []
        self.next_id = 100
        self.raw_inserted = raw_inserted

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "SELECT id" in sql and "FROM processing_queue" in sql:
            return None
        self.next_id += 1
        return self.next_id

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        self.next_id += 1
        if "INSERT INTO raw_documents" in sql:
            return {
                "id": self.next_id,
                "source_hash": args[5],
                "inserted": self.raw_inserted,
            }
        return {"id": self.next_id, "source_hash": args[5] if len(args) > 5 else "hash"}

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "OK"

    async def executemany(self, sql, args):
        self.calls.append(("executemany", sql, tuple(args)))
        return "OK"


class CollectionPersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_save_report_evidence_records_raw_detail_chunks_and_queue_task(self):
        connection = FakeConnection()
        persistence = CollectionPersistence(connection)

        result = await persistence.save_evidence_batch(
            stock_id=1,
            stock_code="005930",
            evidence=[
                RawEvidence(
                    source="REPORT",
                    stock_code="005930",
                    title="Semiconductor report",
                    content="Report body",
                    published_at="2026-06-08T00:00:00+09:00",
                    url="https://example.com/report.pdf",
                    metadata={
                        "source_name": "Example Securities",
                        "securities_firm": "Example Securities",
                        "publish_date": "2026-06-08",
                    },
                )
            ],
            enqueue_task_type="normalize_report",
        )

        self.assertEqual(result["inserted_count"], 1)
        raw_insert = next(call for call in connection.calls if "INSERT INTO raw_documents" in call[1])
        self.assertIsInstance(raw_insert[2][8], datetime)
        self.assertTrue(any("INSERT INTO report_raw_details" in call[1] for call in connection.calls))
        self.assertTrue(any(call[0] == "executemany" for call in connection.calls))
        self.assertTrue(any("INSERT INTO processing_queue" in call[1] for call in connection.calls))

    async def test_save_dart_evidence_enqueues_normalize_task_with_dedupe_identity(self):
        connection = FakeConnection()
        persistence = CollectionPersistence(connection)

        result = await persistence.save_evidence_batch(
            stock_id=1,
            stock_code="005930",
            evidence=[
                RawEvidence(
                    source="DART",
                    stock_code="005930",
                    title="분기보고서",
                    content="DART body",
                    published_at="2026-06-08",
                    url="https://dart.example/receipt",
                    metadata={
                        "source_name": "OpenDART",
                        "receipt_no": "202606080001",
                        "report_name": "분기보고서",
                        "external_id": "202606080001",
                    },
                )
            ],
            collector_type="DART",
            enqueue_task_type="normalize_dart",
        )

        dedupe_call = next(call for call in connection.calls if "SELECT id" in call[1] and "FROM processing_queue" in call[1])
        self.assertIn("source_raw_ids IS NOT DISTINCT FROM", dedupe_call[1])

        enqueue_call = next(call for call in connection.calls if "INSERT INTO processing_queue" in call[1])
        task_context = json.loads(enqueue_call[2][6])
        self.assertEqual(task_context, {"stock_code": "005930", "source_type": "DART"})
        self.assertEqual(result["queued_task_ids"], [104])

    async def test_save_existing_dart_evidence_updates_detail_without_reenqueuing_normalize_task(self):
        connection = FakeConnection(raw_inserted=False)
        persistence = CollectionPersistence(connection)

        result = await persistence.save_evidence_batch(
            stock_id=1,
            stock_code="005930",
            evidence=[
                RawEvidence(
                    source="DART",
                    stock_code="005930",
                    title="분기보고서",
                    content="DART body",
                    published_at="2026-06-08",
                    url="https://dart.example/receipt",
                    metadata={
                        "source_name": "OpenDART",
                        "receipt_no": "202606080001",
                        "report_name": "분기보고서",
                        "external_id": "202606080001",
                    },
                )
            ],
            collector_type="DART",
            enqueue_task_type="normalize_dart",
        )

        self.assertEqual(result["inserted_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertTrue(any("INSERT INTO dart_raw_details" in call[1] for call in connection.calls))
        self.assertFalse(any("INSERT INTO processing_queue" in call[1] for call in connection.calls))

    async def test_save_existing_dart_evidence_with_force_reprocess_reenqueues_normalize_task(self):
        connection = FakeConnection(raw_inserted=False)
        persistence = CollectionPersistence(connection)

        result = await persistence.save_evidence_batch(
            stock_id=1,
            stock_code="005930",
            evidence=[
                RawEvidence(
                    source="DART",
                    stock_code="005930",
                    title="분기보고서",
                    content="DART body",
                    published_at="2026-06-08",
                    url="https://dart.example/receipt",
                    metadata={
                        "source_name": "OpenDART",
                        "receipt_no": "202606080001",
                        "report_name": "분기보고서",
                        "external_id": "202606080001",
                    },
                )
            ],
            collector_type="DART",
            enqueue_task_type="normalize_dart",
            force_reprocess=True,
        )

        self.assertEqual(result["inserted_count"], 0)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(result["reprocessed_count"], 1)
        self.assertTrue(any("INSERT INTO processing_queue" in call[1] for call in connection.calls))

    async def test_save_empty_evidence_finishes_collector_run(self):
        connection = FakeConnection()
        persistence = CollectionPersistence(connection)

        result = await persistence.save_evidence_batch(
            stock_id=1,
            stock_code="005930",
            evidence=[],
            collector_type="REPORT",
        )

        self.assertEqual(result["inserted_count"], 0)
        self.assertTrue(any("UPDATE collector_runs" in call[1] for call in connection.calls))
