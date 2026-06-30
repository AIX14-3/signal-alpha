"""KIPRIS ↔ BigQuery 교차소스 중복제거 — 실 Postgres + 실 KIPRIS XML 파서 end-to-end.

`test_patent_cross_source_dedup.py` 는 source_hash UNIQUE 제약을 in-memory 로
흉내냈다. 이 파일은 그 두 가지 fake 를 모두 *진짜* 로 바꾼 최대 충실도 검증이다:

  1. **실 DB**: 로컬 Postgres(docker compose postgres:16 + 실제 스키마)에 붙어
     진짜 ``raw_documents_source_hash_key`` UNIQUE 제약이 dedup 을 강제한다.
  2. **실 KIPRIS 파서**: 네트워크 호출(``KiprisClient._get_xml``)만 녹화된 실제
     KIPRIS XML 응답으로 바꿔치고, URL 빌드·XML 파싱(``_parse_response``)·레코드
     생성은 전부 진짜 ``KiprisClient`` 가 수행한다 = "KIPRIS 가 이 특허를 실제로
     수집했다" 를 월쿼터 소모 0 으로 재현.

시나리오: BigQuery 백필로 특허를 적재한 뒤, **같은 특허**를 KIPRIS(13자리 표기)로
수집하면 두 번째는 진짜 UNIQUE 제약에 막혀 ``raw_documents`` 에 단 한 행만 남는다.

opt-in 통합 테스트 — 기본 suite 에서는 건너뛴다. 실행:

    docker compose up -d postgres
    docker compose run --rm db-migrate apply --seeds
    cd services/agent-worker
    PATENT_DEDUP_DB_TEST=1 uv run python -m pytest \
        tests/collectors/test_patent_cross_source_dedup_db.py -v

``TEST_DATABASE_URL`` 로 접속 문자열을 덮어쓸 수 있다(기본=compose 로컬 URL).
"""
from __future__ import annotations

import os
import unittest

from app.clients.kipris_client import KiprisClient, KiprisPatentRecord
from app.collectors.patent import PatentCollector

# 같은 특허의 두 소스 표기.
KIPRIS_FORM = "1020210012345"      # 13자리 (2자리 종류 10 + 연도 2021 + 일련 0012345)
BIGQUERY_FORM = "KR-20210012345-A"  # canonical 코어 20210012345 로 수렴

_OPT_IN = os.getenv("PATENT_DEDUP_DB_TEST") == "1"
_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha",
)

# 실제 KIPRIS Plus 응답 형태(대문자 태그)를 그대로 본뜬 녹화 XML.
# KiprisClient._parse_response 가 찾는 <PatentUtilityInfo>/<ApplicationNumber> 사용.
RECORDED_KIPRIS_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE</resultMsg></header>
  <body>
    <TotalSearchCount>1</TotalSearchCount>
    <items>
      <PatentUtilityInfo>
        <ApplicationNumber>{KIPRIS_FORM}</ApplicationNumber>
        <InventionName>반도체 메모리 장치</InventionName>
        <Applicant>삼성전자 주식회사</Applicant>
        <ApplicationDate>20210115</ApplicationDate>
        <InternationalpatentclassificationNumber>H01L</InternationalpatentclassificationNumber>
      </PatentUtilityInfo>
    </items>
  </body>
</response>"""


class _ReplayKiprisClient(KiprisClient):
    """네트워크만 끊고 나머지는 진짜 KIPRIS 클라이언트. ``_get_xml`` 이 녹화 XML 반환."""

    def _get_xml(self, url: str) -> str:  # type: ignore[override]
        return RECORDED_KIPRIS_XML


def _bq_record() -> KiprisPatentRecord:
    return KiprisPatentRecord(
        application_no=BIGQUERY_FORM,
        invention_title="반도체 메모리 장치",
        applicant_name="SAMSUNG ELECTRONICS CO LTD",
        application_date="20210115",
        ipc_code="H01L",
        raw={"source": "google_patents_bigquery", "application_number": BIGQUERY_FORM},
    )


@unittest.skipUnless(_OPT_IN, "set PATENT_DEDUP_DB_TEST=1 (+ local Postgres) to run DB e2e")
class TestCrossSourceDedupRealDB(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from signal_alpha_data_access import DatabaseSettings, create_pool

        try:
            self.pool = await create_pool(DatabaseSettings(database_url=_DB_URL))
        except Exception as exc:  # DB 안 떠 있으면 친절히 skip
            raise unittest.SkipTest(f"local Postgres unreachable at {_DB_URL}: {exc}")

        # 센티넬 종목 1개 삽입(다른 시드와 충돌 없도록 전용 ticker).
        async with self.pool.acquire() as conn:
            self.stock_id = await conn.fetchval(
                """
                INSERT INTO stocks (ticker, name, market, is_active, is_target)
                VALUES ('TST999', 'DEDUP_TEST', 'KOSPI', true, true)
                ON CONFLICT (ticker) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """
            )
            await self._cleanup(conn)  # 이전 잔여 행 제거

    async def asyncTearDown(self) -> None:
        if getattr(self, "pool", None) is None:
            return
        async with self.pool.acquire() as conn:
            await self._cleanup(conn)
            await conn.execute("DELETE FROM stocks WHERE id = $1", self.stock_id)
        await self.pool.close()

    async def _cleanup(self, conn) -> None:
        # raw_documents 삭제 → patent_raw_details 는 ON DELETE CASCADE.
        await conn.execute("DELETE FROM processing_queue WHERE stock_id = $1", self.stock_id)
        await conn.execute("DELETE FROM raw_documents WHERE stock_id = $1", self.stock_id)
        await conn.execute(
            "DELETE FROM collector_runs WHERE collector_type = 'PATENT' AND id = ANY($1::bigint[])",
            getattr(self, "_run_ids", []),
        )

    async def test_kipris_collection_dedupes_against_bigquery_backfill(self) -> None:
        self._run_ids: list[int] = []

        # 1) BigQuery 백필: 특허를 실 DB 에 적재.
        backfiller = PatentCollector(pool=self.pool, client=_ReplayKiprisClient(api_key="unused"), collector_ver="1.0")
        bq = await backfiller.ingest_records(
            stock_id=self.stock_id, records=[_bq_record()], source_name="GOOGLE_PATENTS"
        )
        self._run_ids.append(bq["collector_run_id"])
        self.assertEqual(bq["inserted_count"], 1, "BigQuery 백필이 1건 적재해야 함")

        # 2) KIPRIS 수집(녹화된 실제 XML→진짜 파서): 같은 특허, 13자리 표기.
        collector = PatentCollector(pool=self.pool, client=_ReplayKiprisClient(api_key="unused"), collector_ver="1.0")
        kipris = await collector.run(
            stock_id=self.stock_id,
            stock_code="TST999",
            applicant_name="삼성전자",
            start_date="20210101",
            end_date="20211231",
        )
        self._run_ids.append(kipris["collector_run_id"])

        # KIPRIS 가 실제로 1건 파싱·수집했지만(collected_count=1) 새로 적재하지 않는다.
        self.assertEqual(kipris["collected_count"], 1, "KIPRIS XML 파싱으로 1건 수집됐어야 함")
        self.assertEqual(kipris["inserted_count"], 0, "이미 있는 특허라 새 적재 0")
        self.assertEqual(kipris["skipped_count"] + kipris["requeued_count"], 1)

        # 3) 진짜 UNIQUE 제약 결과: raw_documents 에 PATENT 행은 정확히 1개.
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT source_name, external_id FROM raw_documents WHERE stock_id = $1 AND source_type = 'PATENT'",
                self.stock_id,
            )
        self.assertEqual(len(rows), 1, "교차소스 중복이 단 한 행으로 합쳐져야 함")
        # 먼저 적재한 BigQuery 행이 살아남고, 원본 표기는 external_id 에 보존된다.
        self.assertEqual(rows[0]["source_name"], "GOOGLE_PATENTS")
        self.assertEqual(rows[0]["external_id"], BIGQUERY_FORM)


if __name__ == "__main__":
    unittest.main()
