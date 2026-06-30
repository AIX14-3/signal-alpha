"""KIPRIS ↔ BigQuery 교차소스 중복제거 통합 검증 (DB·GCP·KIPRIS API 불필요).

같은 한국 특허는 소스마다 application_number 표기가 다르다:

  - KIPRIS:   ``1020210012345``    (13자리: 2자리 출원종류 + 4자리 연도 + 7자리 일련)
  - BigQuery: ``KR-20210012345-A``  (국가코드 + 11자리 연도+일련 + 종류코드)

둘 다 ``canonicalize_application_no`` 로 같은 11자리 코어(``20210012345``)로 줄고,
그 키가 ``make_source_hash`` 를 거쳐 ``raw_documents.source_hash`` UNIQUE 제약을
만든다. 따라서 한 소스로 이미 적재된 특허가 다른 소스로 다시 들어오면 두 번째
INSERT 가 UNIQUE 충돌로 막혀 **중복 저장되지 않는다.**

이 파일은 실제 ``PatentCollector.ingest_records``(BigQuery 백필 경로)와
``PatentCollector.run``(KIPRIS 라이브 경로)을 그대로 태우고, 그 사이의 dedup 을
강제하는 *유일한* 제약(``source_hash`` UNIQUE)을 in-memory 로 충실히 흉내내는
``_UniqueAwareConnection`` 으로 검증한다. 외부 의존성(DB/GCP/KIPRIS) 전무 →
KIPRIS 월쿼터가 소진된 상태에서도 "KIPRIS 가 수집된다는 가정"하의 중복방지를
오프라인으로 확인할 수 있다.
"""
from __future__ import annotations

import unittest

import asyncpg.exceptions

from app.clients.kipris_client import KiprisPatentRecord
from app.collectors.patent import PatentCollector
from app.collectors.patent.application_no import canonicalize_application_no
from app.utils.hash_utils import make_source_hash

# 같은 특허의 두 소스 표기 (canonical 코어 20210012345 로 수렴해야 함).
KIPRIS_FORM = "1020210012345"
BIGQUERY_FORM = "KR-20210012345-A"
# 다른 특허(일련번호 상이) — false merge 가 없어야 함을 보일 대조군.
OTHER_FORM = "KR-20210099999-A"


# ---------------------------------------------------------------------------
# source_hash UNIQUE 제약을 흉내내는 stateful fake connection
# ---------------------------------------------------------------------------
class _UniqueAwareConnection:
    """``raw_documents.source_hash`` UNIQUE 제약 + processing_queue 상태를 추적하는
    in-memory asyncpg 대역. 같은 source_hash 로 두 번째 raw INSERT 가 들어오면
    실제 DB 처럼 ``UniqueViolationError`` 를 던진다."""

    def __init__(self) -> None:
        self._next_run_id = 0
        self._next_raw_id = 0
        # source_hash -> raw_id (UNIQUE 제약 본체)
        self.raw_by_hash: dict[str, int] = {}
        # (raw_id, task_type) 활성/성공 task 존재 여부
        self.open_tasks: set[tuple[int, str]] = set()
        self.raw_insert_count = 0          # 실제로 새로 적재된 raw 수
        self.unique_violations = 0         # 충돌이 실제로 발생했는지 (테스트 자가검증)

    # -- read/insert with RETURNING --------------------------------------
    async def fetchval(self, sql: str, *args):
        if "collector_runs" in sql and "INSERT" in sql:
            self._next_run_id += 1
            return self._next_run_id

        if "raw_documents" in sql and "INSERT" in sql:
            source_hash = args[5]  # (stock_id, run_id, type, name, external_id, source_hash, ...)
            if source_hash in self.raw_by_hash:
                self.unique_violations += 1
                raise asyncpg.exceptions.UniqueViolationError("duplicate source_hash")
            self._next_raw_id += 1
            self.raw_by_hash[source_hash] = self._next_raw_id
            self.raw_insert_count += 1
            return self._next_raw_id

        if "raw_documents" in sql and "SELECT" in sql:
            # _requeue_if_unprocessed: 기존 raw 를 source_hash 로 조회
            return self.raw_by_hash.get(args[0])

        if "processing_queue" in sql and "source_raw_ids" in sql:
            # has_open_or_successful_task 프로브 (task_type=$1, raw_id=$2)
            return 1 if (args[1], args[0]) in self.open_tasks else None

        return None

    async def fetchrow(self, sql: str, *args):
        return None

    async def execute(self, sql: str, *args):
        if "processing_queue" in sql and "INSERT" in sql:
            # (stock_id, task_type, [raw_id], task_context) → 활성 task 등록
            task_type, raw_ids = args[1], args[2]
            for raw_id in raw_ids:
                self.open_tasks.add((raw_id, task_type))

    async def fetch(self, sql: str, *args):
        return []  # _fetch_known_categories → 빈 집합

    def transaction(self):
        return _FakeTransaction()


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False  # 예외 전파 (롤백은 fake 라 no-op)


class _Pool:
    def __init__(self, conn: _UniqueAwareConnection) -> None:
        self._conn = conn

    def acquire(self):
        return _PoolCtx(self._conn)


class _PoolCtx:
    def __init__(self, conn: _UniqueAwareConnection) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        return False


class _StaticKiprisClient:
    """단일 페이지를 돌려주는 KIPRIS 클라이언트 대역 (네트워크 없음)."""

    def __init__(self, records: list[KiprisPatentRecord]) -> None:
        self._records = records

    async def search_by_applicant(self, *, applicant, start_date, end_date, page_no):
        if page_no == 1:
            return self._records, len(self._records)
        return [], len(self._records)


def _record(application_no: str) -> KiprisPatentRecord:
    return KiprisPatentRecord(
        application_no=application_no,
        invention_title="반도체 메모리 장치",
        applicant_name="삼성전자",
        application_date="20210115",
        ipc_code="H01L",
        raw={"applicationNumber": application_no},
    )


async def _ingest_via_bigquery(conn, application_no: str) -> dict:
    collector = PatentCollector(pool=_Pool(conn), client=_StaticKiprisClient([]), collector_ver="1.0")
    return await collector.ingest_records(
        stock_id=1, records=[_record(application_no)], source_name="GOOGLE_PATENTS"
    )


async def _collect_via_kipris(conn, application_no: str) -> dict:
    collector = PatentCollector(
        pool=_Pool(conn), client=_StaticKiprisClient([_record(application_no)]), collector_ver="1.0"
    )
    return await collector.run(
        stock_id=1,
        stock_code="005930",
        applicant_name="삼성전자",
        start_date="20210101",
        end_date="20211231",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestCanonicalKeyCollapses(unittest.TestCase):
    """전제: 두 표기가 같은 source_hash 를 만든다(= UNIQUE 제약이 충돌하는 근거)."""

    def test_kipris_and_bigquery_forms_share_source_hash(self):
        self.assertEqual(canonicalize_application_no(KIPRIS_FORM), "20210012345")
        self.assertEqual(canonicalize_application_no(BIGQUERY_FORM), "20210012345")
        self.assertEqual(
            make_source_hash("PATENT", canonicalize_application_no(KIPRIS_FORM)),
            make_source_hash("PATENT", canonicalize_application_no(BIGQUERY_FORM)),
        )

    def test_distinct_patents_have_distinct_source_hash(self):
        self.assertNotEqual(
            make_source_hash("PATENT", canonicalize_application_no(BIGQUERY_FORM)),
            make_source_hash("PATENT", canonicalize_application_no(OTHER_FORM)),
        )


class TestCrossSourceDedup(unittest.IsolatedAsyncioTestCase):
    async def test_bigquery_then_kipris_same_patent_dedupes(self):
        """BigQuery 백필로 먼저 적재 → 같은 특허를 KIPRIS 로 수집 → 두 번째는 skip."""
        conn = _UniqueAwareConnection()

        bq = await _ingest_via_bigquery(conn, BIGQUERY_FORM)
        self.assertEqual(bq["inserted_count"], 1)

        kipris = await _collect_via_kipris(conn, KIPRIS_FORM)
        # 같은 특허이므로 KIPRIS 경로는 새로 적재하지 않는다.
        self.assertEqual(kipris["inserted_count"], 0)
        self.assertEqual(kipris["skipped_count"] + kipris["requeued_count"], 1)

        # raw_documents 에는 단 한 행만 존재하고, UNIQUE 충돌이 실제로 발생했다.
        self.assertEqual(conn.raw_insert_count, 1)
        self.assertEqual(len(conn.raw_by_hash), 1)
        self.assertEqual(conn.unique_violations, 1)

    async def test_kipris_then_bigquery_same_patent_dedupes(self):
        """순서 무관: KIPRIS 로 먼저 → 같은 특허 BigQuery 백필 → 두 번째는 skip."""
        conn = _UniqueAwareConnection()

        kipris = await _collect_via_kipris(conn, KIPRIS_FORM)
        self.assertEqual(kipris["inserted_count"], 1)

        bq = await _ingest_via_bigquery(conn, BIGQUERY_FORM)
        self.assertEqual(bq["inserted_count"], 0)
        self.assertEqual(bq["skipped_count"] + bq["requeued_count"], 1)

        self.assertEqual(conn.raw_insert_count, 1)
        self.assertEqual(conn.unique_violations, 1)

    async def test_distinct_patents_are_not_merged(self):
        """대조군: 일련번호가 다른 특허는 두 소스라도 각각 적재(false merge 없음)."""
        conn = _UniqueAwareConnection()

        await _ingest_via_bigquery(conn, BIGQUERY_FORM)
        bq_other = await _ingest_via_bigquery(conn, OTHER_FORM)

        self.assertEqual(bq_other["inserted_count"], 1)
        self.assertEqual(conn.raw_insert_count, 2)
        self.assertEqual(conn.unique_violations, 0)


if __name__ == "__main__":
    unittest.main()
