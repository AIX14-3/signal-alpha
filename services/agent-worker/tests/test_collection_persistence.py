import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.persistence import CollectionPersistence
from app.schemas.evidence import RawEvidence


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.next_id = 100

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        self.next_id += 1
        return self.next_id

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        self.next_id += 1
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
        self.assertTrue(any("INSERT INTO raw_documents" in call[1] for call in connection.calls))
        self.assertTrue(any("INSERT INTO report_raw_details" in call[1] for call in connection.calls))
        self.assertTrue(any(call[0] == "executemany" for call in connection.calls))
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
