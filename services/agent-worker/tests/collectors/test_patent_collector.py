from __future__ import annotations

import json
import unittest
from datetime import date

import asyncpg.exceptions

from app.clients.kipris_client import KiprisClient, KiprisPatentRecord
from app.collectors.patent import PatentCollector, _build_applicant_aliases, _parse_date, _run_status, _tech_category
from app.utils.hash_utils import make_source_hash


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeKiprisClient:
    def __init__(self, pages: list[tuple[list[KiprisPatentRecord], int]]):
        self._pages = pages
        self._page_size = 100
        self.calls: list[dict] = []

    async def search_by_applicant(self, *, applicant, start_date, end_date, page_no):
        self.calls.append(dict(applicant=applicant, page_no=page_no))
        return self._pages[page_no - 1]


def _make_record(application_no="1020230001234", ipc_code="G06F17/00") -> KiprisPatentRecord:
    return KiprisPatentRecord(
        application_no=application_no,
        invention_title="테스트 특허",
        applicant_name="테스트회사",
        application_date="20230101",
        ipc_code=ipc_code,
        raw={"applicationNumber": application_no},
    )


class FakeConnection:
    """In-memory fake asyncpg connection."""

    def __init__(self, *, raise_unique_on: set[str] | None = None):
        self._raise_unique_on = raise_unique_on or set()
        self.inserts: list[str] = []
        self._run_id = 1
        self._raw_id = 100

    async def fetchval(self, sql, *args):
        if "collector_runs" in sql:
            self.inserts.append("collector_runs")
            return self._run_id
        if "raw_documents" in sql:
            if "raw_documents" in self._raise_unique_on:
                raise asyncpg.exceptions.UniqueViolationError("duplicate key")
            self.inserts.append("raw_documents")
            return self._raw_id
        return None

    async def execute(self, sql, *args):
        if "patent_raw_details" in sql:
            if "patent_raw_details" in self._raise_unique_on:
                raise asyncpg.exceptions.UniqueViolationError("duplicate key")
            self.inserts.append("patent_raw_details")
        elif "processing_queue" in sql:
            self.inserts.append("processing_queue")
        elif "collector_runs" in sql and "UPDATE" in sql:
            self.inserts.append("collector_runs_update")

    async def fetch(self, sql, *args):
        return []

    async def fetchrow(self, sql, *args):
        # _promote_source_if_outranked 의 기존 행 조회(source_hash 기준). 기존 행 source
        # 를 KIPRIS(rank2)로 답하면, KIPRIS 재수집 중복은 incoming_rank<=existing_rank
        # 라 승격하지 않고 기존 requeue/skip 경로를 탄다(GOOGLE_PATENTS 승격은 별도 검증).
        if "raw_documents" in sql and "source_hash" in sql:
            return {"id": self._raw_id, "source_name": "KIPRIS"}
        return None

    def transaction(self):
        return _FakeTransaction(self)


class _FakeTransaction:
    def __init__(self, conn):
        self._conn = conn
        self._committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            pass  # rollback (no-op in fake)
        return False


class _FakeUniqueConnection(FakeConnection):
    """Raises UniqueViolation on raw_documents INSERT (duplicate raw).

    Also answers the F1 re-enqueue lookups: ``SELECT id FROM raw_documents``
    (existing raw by source_hash) and the ``has_open_or_successful_task`` probe on
    processing_queue (controlled by ``existing_task``).
    """

    def __init__(self, *, existing_task: bool = False):
        super().__init__()
        self._existing_task = existing_task

    async def fetchval(self, sql, *args):
        if "raw_documents" in sql and "SELECT" in sql:
            return self._raw_id
        if "raw_documents" in sql:  # INSERT
            raise asyncpg.exceptions.UniqueViolationError("duplicate")
        if "processing_queue" in sql and "source_raw_ids" in sql:
            return 1 if self._existing_task else None
        return await super().fetchval(sql, *args)


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _PoolContext(self._conn)


class _PoolContext:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPatentCollectorInsert(unittest.IsolatedAsyncioTestCase):
    async def test_one_record_inserts_raw_detail_and_queue(self):
        record = _make_record()
        client = FakeKiprisClient(pages=[([record], 1)])
        conn = FakeConnection()
        pool = FakePool(conn)

        collector = PatentCollector(pool=pool, client=client, collector_ver="1.0")
        result = await collector.run(
            stock_id=1,
            stock_code="005930",
            applicant_name="테스트회사",
            start_date="20230101",
            end_date="20231231",
        )

        self.assertEqual(result["inserted_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        self.assertIn("raw_documents", conn.inserts)
        self.assertIn("patent_raw_details", conn.inserts)
        self.assertIn("processing_queue", conn.inserts)

    async def test_duplicate_with_existing_task_increments_skipped_not_failed(self):
        # 중복 raw + 활성/성공 NORMALIZE task 존재 → 진짜 skip(재인큐 안 함).
        record = _make_record()
        client = FakeKiprisClient(pages=[([record], 1)])
        conn = _FakeUniqueConnection(existing_task=True)
        pool = FakePool(conn)

        collector = PatentCollector(pool=pool, client=client, collector_ver="1.0")
        result = await collector.run(
            stock_id=1,
            stock_code="005930",
            applicant_name="테스트회사",
            start_date="20230101",
            end_date="20231231",
        )

        self.assertEqual(result["inserted_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["requeued_count"], 0)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["status"], "success")
        self.assertNotIn("processing_queue", conn.inserts)

    async def test_duplicate_without_task_requeues_for_recovery(self):
        # F1: 중복 raw 인데 활성/성공 task 가 없음 → NORMALIZE 재인큐(자가복구).
        record = _make_record()
        client = FakeKiprisClient(pages=[([record], 1)])
        conn = _FakeUniqueConnection(existing_task=False)
        pool = FakePool(conn)

        collector = PatentCollector(pool=pool, client=client, collector_ver="1.0")
        result = await collector.run(
            stock_id=1,
            stock_code="005930",
            applicant_name="테스트회사",
            start_date="20230101",
            end_date="20231231",
        )

        self.assertEqual(result["inserted_count"], 0)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(result["requeued_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["status"], "success")
        self.assertIn("processing_queue", conn.inserts)

    async def test_status_success_when_all_skipped(self):
        self.assertEqual(_run_status(0, 5, 0), "success")

    async def test_status_partial_when_some_failed(self):
        self.assertEqual(_run_status(3, 0, 1), "partial")

    async def test_status_failed_when_all_records_failed(self):
        self.assertEqual(_run_status(0, 0, 1), "failed")

    async def test_status_success_when_empty_run(self):
        # 빈 결과(0건)는 실패가 아니다 — 진짜 에러는 호출자가 except 에서 직접
        # 'failed' 로 기록한다. (0,0,0) 을 failed 로 세면 실패율이 부풀려진다.
        self.assertEqual(_run_status(0, 0, 0), "success")

    async def test_collector_does_not_write_to_forbidden_tables(self):
        record = _make_record()
        client = FakeKiprisClient(pages=[([record], 1)])
        conn = FakeConnection()
        pool = FakePool(conn)

        collector = PatentCollector(pool=pool, client=client, collector_ver="1.0")
        await collector.run(
            stock_id=1,
            stock_code="005930",
            applicant_name="테스트회사",
            start_date="20230101",
            end_date="20231231",
        )

        forbidden = {"source_documents", "signal_events", "signal_metrics", "analysis_results", "agent_results", "final_signals"}
        written = set(conn.inserts)
        self.assertTrue(written.isdisjoint(forbidden), f"Wrote to forbidden tables: {written & forbidden}")

    async def test_generated_applicant_aliases_are_deduplicated_by_application_no(self):
        record = _make_record()
        client = FakeKiprisClient(pages=[([record], 1)])
        conn = FakeConnection()
        pool = FakePool(conn)

        collector = PatentCollector(pool=pool, client=client, collector_ver="1.0")
        result = await collector.run(
            stock_id=2,
            stock_code="000660",
            stock_name="SK하이닉스",
            start_date="20230101",
            end_date="20231231",
        )

        applicants = [call["applicant"] for call in client.calls]
        self.assertIn("SK하이닉스", applicants)
        self.assertIn("에스케이하이닉스", applicants)
        self.assertIn("에스케이하이닉스 주식회사", applicants)
        self.assertEqual(result["collected_count"], 1)
        self.assertEqual(result["inserted_count"], 1)


class TestTechCategory(unittest.TestCase):
    def test_g06_maps_to_ai_software(self):
        self.assertEqual(_tech_category("G06F17/00"), "AI/Software")

    def test_h04_maps_to_communications(self):
        self.assertEqual(_tech_category("H04L"), "Communications")

    def test_h01l_maps_to_semiconductors(self):
        self.assertEqual(_tech_category("H01L"), "Semiconductors")

    def test_none_ipc_returns_none(self):
        self.assertIsNone(_tech_category(None))

    def test_null_ipc_means_is_new_category_false(self):
        """When tech_category is None, is_new_category must be False."""
        cat = _tech_category(None)
        is_new = (cat is not None) and (cat not in set())
        self.assertFalse(is_new)


class TestApplicantAliases(unittest.TestCase):
    def test_builds_generic_aliases_from_stock_name(self):
        aliases = _build_applicant_aliases(
            stock_name="SK하이닉스",
            applicant_name=None,
            applicant_names=None,
        )

        self.assertIn("SK하이닉스", aliases)
        self.assertIn("SK하이닉스 주식회사", aliases)
        self.assertIn("에스케이하이닉스", aliases)
        self.assertIn("에스케이하이닉스 주식회사", aliases)

    def test_uses_external_aliases_without_stock_specific_code(self):
        aliases = _build_applicant_aliases(
            stock_name="Example Tech",
            applicant_name=None,
            applicant_names=["Example Technologies Inc."],
        )

        self.assertIn("Example Tech", aliases)
        self.assertIn("Example Technologies", aliases)


class TestSourceHash(unittest.TestCase):
    def test_patent_hash_uses_only_application_no(self):
        h1 = make_source_hash("PATENT", "1020230001234")
        h2 = make_source_hash("PATENT", "1020230001234")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_different_application_no_produces_different_hash(self):
        h1 = make_source_hash("PATENT", "1020230001234")
        h2 = make_source_hash("PATENT", "1020230009999")
        self.assertNotEqual(h1, h2)

    def test_hash_is_lowercase_hex(self):
        h = make_source_hash("PATENT", "ABC123")
        self.assertTrue(all(c in "0123456789abcdef" for c in h))


class FakeConnectionCapturing(FakeConnection):
    """Captures the task_context JSON passed to processing_queue INSERT."""

    def __init__(self):
        super().__init__()
        self.captured_task_context: dict | None = None
        self.last_run_status: str | None = None

    async def execute(self, sql, *args):
        if "processing_queue" in sql and len(args) >= 4:
            self.captured_task_context = json.loads(args[3])
        if "collector_runs" in sql and "UPDATE" in sql and len(args) >= 2:
            self.last_run_status = args[1]
        await super().execute(sql, *args)


class FakeKiprisClientRaising:
    async def search_by_applicant(self, **kwargs):
        raise RuntimeError("KIPRIS API unavailable")


class TestPatentTaskContext(unittest.IsolatedAsyncioTestCase):
    async def test_task_context_contains_required_keys(self):
        record = _make_record()
        client = FakeKiprisClient(pages=[([record], 1)])
        conn = FakeConnectionCapturing()
        pool = FakePool(conn)

        collector = PatentCollector(pool=pool, client=client, collector_ver="2.5")
        await collector.run(
            stock_id=1,
            stock_code="005930",
            applicant_name="테스트회사",
            start_date="20230101",
            end_date="20231231",
        )

        ctx = conn.captured_task_context
        self.assertIsNotNone(ctx)
        self.assertIn("collector_run_id", ctx)
        self.assertIn("source_type", ctx)
        self.assertIn("collector_ver", ctx)
        self.assertEqual(ctx["source_type"], "PATENT")
        self.assertEqual(ctx["collector_ver"], "2.5")


class TestPatentApiError(unittest.IsolatedAsyncioTestCase):
    async def test_api_error_sets_status_failed(self):
        conn = FakeConnectionCapturing()
        pool = FakePool(conn)
        collector = PatentCollector(pool=pool, client=FakeKiprisClientRaising(), collector_ver="1.0")

        with self.assertRaises(RuntimeError):
            await collector.run(
                stock_id=1,
                stock_code="005930",
                applicant_name="테스트회사",
                start_date="20230101",
                end_date="20231231",
            )

        self.assertEqual(conn.last_run_status, "failed")


class TestParseDate(unittest.TestCase):
    def test_yyyymmdd_string(self):
        self.assertEqual(_parse_date("20230115"), date(2023, 1, 15))

    def test_none_returns_today(self):
        self.assertEqual(_parse_date(None), date.today())

    def test_iso_format(self):
        self.assertEqual(_parse_date("2023-01-15"), date(2023, 1, 15))


class TestKiprisClientParsing(unittest.TestCase):
    def test_parses_current_kipris_capitalized_tags(self):
        xml = """
        <response>
          <header><resultCode>00</resultCode></header>
          <body>
            <TotalSearchCount>1</TotalSearchCount>
            <items>
              <PatentUtilityInfo>
                <ApplicationNumber>1020260000001</ApplicationNumber>
                <InventionName>메모리 장치</InventionName>
                <Applicant>에스케이하이닉스 주식회사</Applicant>
                <ApplicationDate>20260501</ApplicationDate>
                <InternationalpatentclassificationNumber>H01L</InternationalpatentclassificationNumber>
              </PatentUtilityInfo>
            </items>
          </body>
        </response>
        """

        records, total = KiprisClient(api_key="test")._parse_response(xml)

        self.assertEqual(total, 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].application_no, "1020260000001")
        self.assertEqual(records[0].invention_title, "메모리 장치")
        self.assertEqual(records[0].applicant_name, "에스케이하이닉스 주식회사")
        self.assertEqual(records[0].ipc_code, "H01L")
