import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.persistence import CollectionPersistence, _source_hash
from app.schemas.evidence import RawEvidence


def _dart_evidence(receipt_no: str) -> RawEvidence:
    return RawEvidence(
        source="DART",
        stock_code="005930",
        title="분기보고서",
        content="DART body",
        published_at="2026-06-08",
        url=f"https://dart.example/{receipt_no}",
        metadata={
            "source_name": "OpenDART",
            "receipt_no": receipt_no,
            "report_name": "분기보고서",
            "external_id": receipt_no,
        },
    )


class _FakeTransaction:
    """asyncpg connection.transaction() 흉내 — BEGIN/COMMIT/ROLLBACK 기록."""

    def __init__(self, connection):
        self._connection = connection

    async def __aenter__(self):
        self._connection.calls.append(("BEGIN", "", ()))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._connection.calls.append(("ROLLBACK" if exc_type else "COMMIT", "", ()))
        return False


class FakeConnection:
    def __init__(self, *, raw_inserted=True, fail_enqueue_on_attempt=None):
        self.calls = []
        self.next_id = 100
        self.raw_inserted = raw_inserted
        # N번째 processing_queue INSERT 에서 실패를 주입(1-based). None 이면 실패 없음.
        self.fail_enqueue_on_attempt = fail_enqueue_on_attempt
        self.enqueue_attempts = 0

    def transaction(self):
        return _FakeTransaction(self)

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "SELECT id" in sql and "FROM processing_queue" in sql:
            return None
        if "INSERT INTO processing_queue" in sql:
            self.enqueue_attempts += 1
            if self.enqueue_attempts == self.fail_enqueue_on_attempt:
                raise RuntimeError("enqueue failed")
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

    async def test_enqueue_failure_rolls_back_record_and_fails_run(self):
        # 단일 record 의 enqueue 가 실패하면 그 record 의 raw/detail 까지 롤백되고
        # (고아 방지), usable 0 + 실패 → run=failed 로 기록 후 raise.
        connection = FakeConnection(fail_enqueue_on_attempt=1)
        persistence = CollectionPersistence(connection)

        with self.assertRaises(RuntimeError):
            await persistence.save_evidence_batch(
                stock_id=1,
                stock_code="005930",
                evidence=[_dart_evidence("202606080001")],
                collector_type="DART",
                enqueue_task_type="normalize_dart",
            )

        self.assertIn(("ROLLBACK", "", ()), connection.calls)
        self.assertNotIn(("COMMIT", "", ()), connection.calls)
        finish = next(c for c in connection.calls if c[0] == "execute" and "UPDATE collector_runs" in c[1])
        self.assertEqual(finish[2][1], "failed")  # status = $2
        self.assertEqual(finish[2][5], 1)  # failed_count = $6

    async def test_partial_failure_commits_good_record_and_returns_partial(self):
        # 2건 중 2번째 enqueue 실패 → 1번째는 커밋, 2번째는 롤백. partial 로 정상 반환.
        connection = FakeConnection(fail_enqueue_on_attempt=2)
        persistence = CollectionPersistence(connection)

        result = await persistence.save_evidence_batch(
            stock_id=1,
            stock_code="005930",
            evidence=[_dart_evidence("202606080001"), _dart_evidence("202606080002")],
            collector_type="DART",
            enqueue_task_type="normalize_dart",
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["inserted_count"], 1)
        self.assertEqual(len(result["queued_task_ids"]), 1)
        self.assertIn(("COMMIT", "", ()), connection.calls)
        self.assertIn(("ROLLBACK", "", ()), connection.calls)
        finish = next(c for c in connection.calls if c[0] == "execute" and "UPDATE collector_runs" in c[1])
        self.assertEqual(finish[2][1], "partial")

    def test_source_hash_normalizes_whitespace_and_case(self):
        messy = RawEvidence(
            source="DART",
            stock_code="005930",
            title="  Quarterly Report  ",
            content="Body",
            published_at="2026-06-08",
            url="HTTPS://Example.com/A",
            metadata={},
        )
        clean = RawEvidence(
            source="dart",
            stock_code="005930",
            title="quarterly report",
            content="body",
            published_at="2026-06-08",
            url="https://example.com/a",
            metadata={},
        )
        self.assertEqual(_source_hash(messy), _source_hash(clean))
        self.assertEqual(len(_source_hash(messy)), 64)
