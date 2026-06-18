from __future__ import annotations

import json
import unittest

import asyncpg.exceptions

from app.clients.naver_datalab_client import NaverDataLabClient, NaverDataLabRecord
from app.collectors.datalab import (
    DataLabCollector,
    CHANGE_PCT_SPIKE_THRESHOLD,
    SEARCH_INDEX_SPIKE_THRESHOLD,
    _compute_change_pct,
    _run_status,
)
from app.utils.hash_utils import make_source_hash


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeNaverClient:
    def __init__(self, records: list[NaverDataLabRecord]):
        self._records = records
        self.calls: list[dict] = []

    async def search(self, *, keyword_group, keywords, start_date, end_date, time_unit, device, gender, ages):
        self.calls.append(dict(keyword_group=keyword_group, time_unit=time_unit))
        return self._records


def _make_record(keyword="삼성전자", period="2024-01-01", ratio=75.0) -> NaverDataLabRecord:
    return NaverDataLabRecord(
        keyword_group="삼성",
        keyword=keyword,
        period=period,
        ratio=ratio,
        raw_data={"period": period, "ratio": ratio},
    )


class FakeConnection:
    def __init__(self, *, raise_unique_on_raw: bool = False, existing_task: bool = False):
        self._raise_unique_on_raw = raise_unique_on_raw
        self._existing_task = existing_task
        self.inserts: list[str] = []
        self._run_id = 1
        self._raw_id = 200

    async def fetchval(self, sql, *args):
        if "collector_runs" in sql and "INSERT" in sql:
            self.inserts.append("collector_runs")
            return self._run_id
        # F1 재인큐 경로: 기존 raw 조회(롤백 후 source_hash 로 찾음).
        if "datalab_raw_documents" in sql and "SELECT" in sql:
            return self._raw_id
        if "datalab_raw_documents" in sql:  # INSERT
            if self._raise_unique_on_raw:
                raise asyncpg.exceptions.UniqueViolationError("duplicate key")
            self.inserts.append("datalab_raw_documents")
            return self._raw_id
        # has_open_or_successful_task 프로브.
        if "processing_queue" in sql and "source_raw_ids" in sql:
            return 1 if self._existing_task else None
        return None

    async def execute(self, sql, *args):
        if "datalab_raw_details" in sql:
            self.inserts.append("datalab_raw_details")
        elif "processing_queue" in sql:
            self.inserts.append("processing_queue")
        elif "collector_runs" in sql and "UPDATE" in sql:
            self.inserts.append("collector_runs_update")

    async def fetch(self, sql, *args):
        return []

    def transaction(self):
        return _FakeTx()


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Ctx(self._conn)


class _Ctx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDataLabCollectorInsert(unittest.IsolatedAsyncioTestCase):
    async def test_one_record_inserts_raw_detail_and_queue(self):
        records = [_make_record()]
        client = FakeNaverClient(records)
        conn = FakeConnection()
        pool = FakePool(conn)

        collector = DataLabCollector(pool=pool, client=client, collector_ver="1.0")
        result = await collector.run(
            category_id=1,
            category_name="삼성",
            keyword_group="삼성",
            keywords=["삼성전자"],
            start_date="2024-01-01",
            end_date="2024-01-31",
        )

        self.assertEqual(result["inserted_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        self.assertIn("datalab_raw_documents", conn.inserts)
        self.assertIn("datalab_raw_details", conn.inserts)
        self.assertIn("processing_queue", conn.inserts)

    async def test_multiple_dates_each_create_one_row(self):
        records = [
            _make_record(period="2024-01-01", ratio=70.0),
            _make_record(period="2024-01-02", ratio=75.0),
            _make_record(period="2024-01-03", ratio=80.0),
        ]
        client = FakeNaverClient(records)
        conn = FakeConnection()
        pool = FakePool(conn)

        collector = DataLabCollector(pool=pool, client=client, collector_ver="1.0")
        result = await collector.run(
            category_id=1,
            category_name="삼성",
            keyword_group="삼성",
            keywords=["삼성전자"],
            start_date="2024-01-01",
            end_date="2024-01-03",
        )

        self.assertEqual(result["inserted_count"], 3)
        self.assertEqual(conn.inserts.count("datalab_raw_documents"), 3)
        self.assertEqual(conn.inserts.count("datalab_raw_details"), 3)
        self.assertEqual(conn.inserts.count("processing_queue"), 3)

    async def test_duplicate_with_existing_task_increments_skipped_not_failed(self):
        # 중복 raw + 활성/성공 NORMALIZE task 존재 → 진짜 skip(재인큐 안 함).
        records = [_make_record()]
        client = FakeNaverClient(records)
        conn = FakeConnection(raise_unique_on_raw=True, existing_task=True)
        pool = FakePool(conn)

        collector = DataLabCollector(pool=pool, client=client, collector_ver="1.0")
        result = await collector.run(
            category_id=1,
            category_name="삼성",
            keyword_group="삼성",
            keywords=["삼성전자"],
        )

        self.assertEqual(result["inserted_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["requeued_count"], 0)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["status"], "success")
        self.assertNotIn("processing_queue", conn.inserts)

    async def test_duplicate_without_task_requeues_for_recovery(self):
        # F1: 중복 raw 인데 활성/성공 task 가 없음 → NORMALIZE 재인큐(자가복구).
        records = [_make_record()]
        client = FakeNaverClient(records)
        conn = FakeConnection(raise_unique_on_raw=True, existing_task=False)
        pool = FakePool(conn)

        collector = DataLabCollector(pool=pool, client=client, collector_ver="1.0")
        result = await collector.run(
            category_id=1,
            category_name="삼성",
            keyword_group="삼성",
            keywords=["삼성전자"],
        )

        self.assertEqual(result["inserted_count"], 0)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(result["requeued_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["status"], "success")
        self.assertIn("processing_queue", conn.inserts)

    async def test_collector_does_not_write_to_forbidden_tables(self):
        records = [_make_record()]
        client = FakeNaverClient(records)
        conn = FakeConnection()
        pool = FakePool(conn)

        collector = DataLabCollector(pool=pool, client=client, collector_ver="1.0")
        await collector.run(
            category_id=1,
            category_name="삼성",
            keyword_group="삼성",
            keywords=["삼성전자"],
        )

        forbidden = {"raw_documents", "source_documents", "signal_events", "signal_metrics",
                     "analysis_results", "agent_results", "final_signals"}
        self.assertTrue(set(conn.inserts).isdisjoint(forbidden))

    async def test_status_all_skipped_is_success(self):
        self.assertEqual(_run_status(0, 3, 0), "success")


class TestChangePct(unittest.TestCase):
    def test_normal_increase(self):
        self.assertAlmostEqual(_compute_change_pct(110, 100), 10.0)

    def test_normal_decrease(self):
        self.assertAlmostEqual(_compute_change_pct(90, 100), -10.0)

    def test_previous_none_returns_none(self):
        self.assertIsNone(_compute_change_pct(100, None))

    def test_previous_zero_current_positive_returns_999(self):
        self.assertAlmostEqual(_compute_change_pct(50, 0), 999.99)

    def test_previous_zero_current_zero_returns_zero(self):
        self.assertAlmostEqual(_compute_change_pct(0, 0), 0.0)

    def test_no_change(self):
        self.assertAlmostEqual(_compute_change_pct(100, 100), 0.0)


class TestPeriodTypeMapping(unittest.TestCase):
    def test_date_maps_to_daily(self):
        self.assertEqual(NaverDataLabClient.time_unit_to_period_type("date"), "daily")

    def test_week_maps_to_weekly(self):
        self.assertEqual(NaverDataLabClient.time_unit_to_period_type("week"), "weekly")

    def test_month_maps_to_monthly(self):
        self.assertEqual(NaverDataLabClient.time_unit_to_period_type("month"), "monthly")

    def test_unknown_defaults_to_daily(self):
        self.assertEqual(NaverDataLabClient.time_unit_to_period_type("unknown"), "daily")


class CapturingNaverDataLabClient(NaverDataLabClient):
    def __init__(self):
        self.requests: list[dict] = []

    def _post_json(self, body):
        self.requests.append(body)
        keyword = body["keywordGroups"][0]["keywords"][0]
        return {
            "results": [
                {
                    "title": keyword,
                    "keywords": [keyword],
                    "data": [{"period": "2024-01-01", "ratio": 100.0}],
                }
            ]
        }


class TestNaverDataLabClientPerKeyword(unittest.IsolatedAsyncioTestCase):
    async def test_search_calls_api_once_per_keyword(self):
        client = CapturingNaverDataLabClient()

        records = await client.search(
            keyword_group="HBM",
            keywords=["HBM", "HBM3E", "HBM4"],
            start_date="2024-01-01",
            end_date="2024-01-01",
        )

        self.assertEqual(len(client.requests), 3)
        self.assertEqual(
            [req["keywordGroups"][0]["keywords"] for req in client.requests],
            [["HBM"], ["HBM3E"], ["HBM4"]],
        )
        self.assertEqual([record.keyword for record in records], ["HBM", "HBM3E", "HBM4"])
        self.assertEqual([record.keyword_group for record in records], ["HBM", "HBM", "HBM"])


class FakeConnectionCapturing(FakeConnection):
    def __init__(self):
        super().__init__()
        self.captured_task_context: dict | None = None
        self.last_run_status: str | None = None
        self.stored_period_type: str | None = None

    async def execute(self, sql, *args):
        if "processing_queue" in sql and len(args) >= 4:
            self.captured_task_context = json.loads(args[3])
        if "collector_runs" in sql and "UPDATE" in sql and len(args) >= 2:
            self.last_run_status = args[1]
        if "datalab_raw_details" in sql and len(args) >= 9:
            self.stored_period_type = args[8]
        await super().execute(sql, *args)


class FakeNaverClientRaising:
    async def search(self, **kwargs):
        raise RuntimeError("Naver API unavailable")


class TestDataLabTaskContext(unittest.IsolatedAsyncioTestCase):
    async def test_task_context_contains_required_keys(self):
        records = [_make_record()]
        conn = FakeConnectionCapturing()
        pool = FakePool(conn)
        collector = DataLabCollector(pool=pool, client=FakeNaverClient(records), collector_ver="3.1")
        await collector.run(category_id=5, category_name="HBM", keyword_group="HBM", keywords=["HBM"])

        ctx = conn.captured_task_context
        self.assertIsNotNone(ctx)
        self.assertIn("collector_run_id", ctx)
        self.assertIn("source_type", ctx)
        self.assertIn("collector_ver", ctx)
        self.assertIn("category_id", ctx)
        self.assertIn("keyword", ctx)
        self.assertEqual(ctx["source_type"], "DATALAB")
        self.assertEqual(ctx["collector_ver"], "3.1")
        self.assertEqual(ctx["category_id"], 5)


class TestDataLabApiError(unittest.IsolatedAsyncioTestCase):
    async def test_api_error_sets_status_failed(self):
        conn = FakeConnectionCapturing()
        pool = FakePool(conn)
        collector = DataLabCollector(pool=pool, client=FakeNaverClientRaising(), collector_ver="1.0")

        with self.assertRaises(RuntimeError):
            await collector.run(category_id=1, category_name="HBM", keyword_group="HBM", keywords=["HBM"])

        self.assertEqual(conn.last_run_status, "failed")


class TestDataLabTimeUnitRoundTrip(unittest.IsolatedAsyncioTestCase):
    async def _run_with_time_unit(self, time_unit: str):
        records = [_make_record()]
        client = FakeNaverClient(records)
        conn = FakeConnectionCapturing()
        pool = FakePool(conn)
        collector = DataLabCollector(pool=pool, client=client, collector_ver="1.0")
        await collector.run(
            category_id=1, category_name="HBM", keyword_group="HBM",
            keywords=["HBM"], time_unit=time_unit,
        )
        api_time_unit = client.calls[0]["time_unit"] if client.calls else None
        return api_time_unit, conn.stored_period_type

    async def test_date_round_trip(self):
        api_tu, db_pt = await self._run_with_time_unit("date")
        self.assertEqual(api_tu, "date")
        self.assertEqual(db_pt, "daily")

    async def test_week_round_trip(self):
        api_tu, db_pt = await self._run_with_time_unit("week")
        self.assertEqual(api_tu, "week")
        self.assertEqual(db_pt, "weekly")

    async def test_month_round_trip(self):
        api_tu, db_pt = await self._run_with_time_unit("month")
        self.assertEqual(api_tu, "month")
        self.assertEqual(db_pt, "monthly")


class TestDataLabSourceHash(unittest.TestCase):
    def test_hash_includes_all_required_parts(self):
        h = make_source_hash("DATALAB", 1, "삼성전자", "2024-01-01", "daily", "all", "all", "all")
        self.assertEqual(len(h), 64)

    def test_different_date_produces_different_hash(self):
        h1 = make_source_hash("DATALAB", 1, "삼성전자", "2024-01-01", "daily", "all", "all", "all")
        h2 = make_source_hash("DATALAB", 1, "삼성전자", "2024-01-02", "daily", "all", "all", "all")
        self.assertNotEqual(h1, h2)

    def test_none_parts_treated_as_empty_string(self):
        h1 = make_source_hash("DATALAB", None, "kw", "2024-01-01", "daily", "all", "all", "all")
        h2 = make_source_hash("DATALAB", "", "kw", "2024-01-01", "daily", "all", "all", "all")
        self.assertEqual(h1, h2)

    def test_is_spike_true_when_change_above_threshold(self):
        change = _compute_change_pct(current=160, previous=100)
        is_spike = change is not None and change >= CHANGE_PCT_SPIKE_THRESHOLD
        self.assertTrue(is_spike)  # 60% >= 50%

    def test_is_spike_false_when_small_change(self):
        change = _compute_change_pct(current=105, previous=100)
        is_spike = change is not None and change >= CHANGE_PCT_SPIKE_THRESHOLD
        self.assertFalse(is_spike)  # 5% < 50%

    def test_is_spike_true_when_search_index_high(self):
        is_spike = 85.0 >= SEARCH_INDEX_SPIKE_THRESHOLD
        self.assertTrue(is_spike)

    def test_is_spike_false_when_search_index_below_threshold(self):
        is_spike = 70.0 >= SEARCH_INDEX_SPIKE_THRESHOLD
        self.assertFalse(is_spike)
