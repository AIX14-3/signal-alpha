"""Report RAG — 실 Postgres + pgvector end-to-end (opt-in 통합 테스트).

단위 테스트(test_report_embed_handler / test_report_retriever)는 fake conn 으로 SQL *문자열*만
검증한다. 이 파일은 **진짜 pgvector** 에 대고 실제 SQL 이 도는지 확인한다 — fake 로는 절대 못 잡는
것들:

  1. ``$N::vector`` 리터럴 캐스팅이 실제 ``vector(768)`` 컬럼에 들어간다.
  2. ``embedding <=> $1::vector`` 코사인 거리 + HNSW 인덱스로 Top-K 가 정렬돼 돌아온다.
  3. ``SET LOCAL hnsw.ef_search`` 가 에러 없이 먹는다(리콜 방어).
  4. 종목 스코프 조인(report_chunks→raw_documents.stock_id)이 다른 종목 청크를 안 섞는다.
  5. ``ON CONFLICT (report_raw_detail_id, chunk_index) DO NOTHING`` 멱등 재적재.

Gemini 키 불필요 — 임베더만 가짜(결정론 768차원). 스키마는 **실제 마이그레이션 결과**를 쓴다(무드리프트).

opt-in — 기본 suite 에서 skip. 실행:
    docker compose up -d postgres
    docker compose run --rm db-migrate apply --seeds
    cd services/agent-worker
    REPORT_RAG_DB_TEST=1 uv run python -m pytest tests/test_report_rag_db.py -v

``TEST_DATABASE_URL`` 로 접속 문자열 덮어쓰기 가능(기본=compose 로컬 URL).
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.report import tasks as report_tasks
from app.orchestrator.report.tasks import ReportEmbedTaskHandler

_OPT_IN = os.getenv("REPORT_RAG_DB_TEST") == "1"
_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha",
)


def _v(a: float, b: float) -> list[float]:
    """768차원 임베딩 — 앞 2성분만 세팅해 코사인 순서를 결정적으로 만든다."""
    vec = [0.0] * 768
    vec[0] = a
    vec[1] = b
    return vec


class _FakeEmbedder:
    """embed_batch=적재용 벡터열, embed=질의 벡터. 실제 API 대체(키 불필요)."""

    dim = 768
    model = "fake-embed"

    def __init__(self, *, batch: list[list[float]] | None = None, query: list[float] | None = None) -> None:
        self._batch = batch or []
        self._query = query or _v(1.0, 0.0)

    async def embed(self, text: str) -> list[float]:
        return list(self._query)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [list(v) for v in self._batch]


@unittest.skipUnless(_OPT_IN, "set REPORT_RAG_DB_TEST=1 (+ local pgvector Postgres) to run")
class ReportRagPgvectorDBTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from signal_alpha_data_access import DatabaseSettings, create_pool

        try:
            self.pool = await create_pool(DatabaseSettings(database_url=_DB_URL))
        except Exception as exc:  # DB 안 떠 있으면 친절히 skip
            raise unittest.SkipTest(f"local Postgres unreachable at {_DB_URL}: {exc}")

        self._raw_ids: list[int] = []
        self._run_ids: list[int] = []
        async with self.pool.acquire() as conn:
            ext = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname='vector'")
            if ext is None:
                raise unittest.SkipTest("pgvector extension not installed on target DB")
            self._pgvector_version = str(ext)
            self.stock_a = await self._make_stock(conn, "TSTRAGA", "RAG_A")
            self.stock_b = await self._make_stock(conn, "TSTRAGB", "RAG_B")

    async def asyncTearDown(self) -> None:
        if getattr(self, "pool", None) is None:
            return
        stock_ids = [sid for sid in (getattr(self, "stock_a", None), getattr(self, "stock_b", None)) if sid]
        async with self.pool.acquire() as conn:
            # 순서 주의: raw_documents 먼저(→ report_raw_details → report_chunks 까지 ON DELETE CASCADE),
            # 그다음 우리가 만든 collector_runs(참조 해제 후), 마지막에 stocks.
            for stock_id in stock_ids:
                await conn.execute("DELETE FROM processing_queue WHERE stock_id = $1", stock_id)
                await conn.execute("DELETE FROM raw_documents WHERE stock_id = $1", stock_id)
            if self._run_ids:
                await conn.execute("DELETE FROM collector_runs WHERE id = ANY($1::bigint[])", self._run_ids)
            for stock_id in stock_ids:
                await conn.execute("DELETE FROM stocks WHERE id = $1", stock_id)
        await self.pool.close()

    async def _make_stock(self, conn, ticker: str, name: str) -> int:
        return await conn.fetchval(
            """
            INSERT INTO stocks (ticker, name, market, is_active, is_target)
            VALUES ($1, $2, 'KOSPI', true, true)
            ON CONFLICT (ticker) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            ticker,
            name,
        )

    async def _seed_report(self, conn, *, stock_id: int, broker: str, text: str) -> int:
        """실 스키마에 리포트 1건 시딩(파싱 완료 상태). raw_document_id 반환."""
        from signal_alpha_data_access.repositories import CollectionRepository

        repo = CollectionRepository(conn)
        run_id = await repo.create_collector_run("REPORT", "batch")
        self._run_ids.append(int(run_id))
        digest = f"{stock_id}|{broker}|{text}"
        row = await repo.upsert_raw_document(
            stock_id=stock_id,
            collector_run_id=run_id,
            source_type="REPORT",
            source_name=broker,
            external_id=digest,
            source_hash=digest,
            title="통합테스트 리포트",
            source_url=None,
            published_at=date(2026, 6, 24),
            collector_ver="test",
        )
        raw_document_id = int(row["id"])
        await repo.upsert_report_detail(
            raw_document_id=raw_document_id,
            stock_id=stock_id,
            securities_firm=broker,
            publish_date=date(2026, 6, 24),
            has_pdf=False,
            pdf_url=None,
            parsing_status="success",
            extra_payload={},
        )
        # s3_key 없이 extracted_text 로만 임베딩(스토리지 불필요). parsing_status 확정.
        await conn.execute(
            "UPDATE report_raw_details SET parsing_status='success', s3_key=NULL, extracted_text=$2 WHERE raw_document_id=$1",
            raw_document_id,
            text,
        )
        self._raw_ids.append(raw_document_id)
        return raw_document_id

    async def _embed(self, conn, *, raw_document_id: int, chunks: list[str], vectors: list[list[float]]) -> dict:
        orig = report_tasks._chunk_report_text
        report_tasks._chunk_report_text = lambda _text: list(chunks)
        try:
            handler = ReportEmbedTaskHandler(
                connection=conn, settings=object(), embedder=_FakeEmbedder(batch=vectors)
            )
            return await handler({"task_context": {"raw_document_id": raw_document_id}})
        finally:
            report_tasks._chunk_report_text = orig

    async def test_embed_and_retrieve_end_to_end(self) -> None:
        from app.agents.report.retriever import retrieve

        chunks = ["첫 문단 A(가장 가까움)", "둘째 문단 B(가장 멂)", "셋째 문단 C(중간)"]
        vectors = [_v(1.0, 0.0), _v(0.0, 1.0), _v(0.8, 0.6)]

        async with self.pool.acquire() as conn:
            rid_a = await self._seed_report(conn, stock_id=self.stock_a, broker="신한", text="본문A")
            # 다른 종목 리포트(스코프 격리 확인용).
            rid_b = await self._seed_report(conn, stock_id=self.stock_b, broker="KB", text="본문B")

            # 1) 실제 embed INSERT ($4::vector) — report_chunks 에 3행 적재.
            result = await self._embed(conn, raw_document_id=rid_a, chunks=chunks, vectors=vectors)
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["chunks"], 3)
            count = await conn.fetchval(
                "SELECT count(*) FROM report_chunks WHERE report_raw_detail_id=$1", rid_a
            )
            self.assertEqual(count, 3)

            # 다른 종목에도 1청크 적재(스코프 테스트용).
            await self._embed(conn, raw_document_id=rid_b, chunks=["종목B 문단"], vectors=[_v(1.0, 0.0)])

            # 2) 멱등: 재실행은 already_embedded, 카운트 불변.
            again = await self._embed(conn, raw_document_id=rid_a, chunks=chunks, vectors=vectors)
            self.assertEqual(again["status"], "already_embedded")
            self.assertEqual(
                await conn.fetchval("SELECT count(*) FROM report_chunks WHERE report_raw_detail_id=$1", rid_a),
                3,
            )

            # 3) 실제 retriever: 코사인 <=> + ef_search + 종목 스코프.
            rows = await retrieve(
                conn, stock_id=self.stock_a, query="A 문단", embedder=_FakeEmbedder(query=_v(1.0, 0.0)), top_k=3
            )

        self.assertEqual(len(rows), 3)
        # 질의 벡터 = vecA → 가장 가까운 청크가 A.
        self.assertEqual(rows[0]["chunk_text"], "첫 문단 A(가장 가까움)")
        self.assertEqual(rows[0]["broker"], "신한")
        # 종목 스코프: stock_a 결과에 stock_b 청크가 절대 안 섞인다.
        self.assertTrue(all(r["raw_document_id"] == rid_a for r in rows))
        self.assertNotIn("종목B 문단", [r["chunk_text"] for r in rows])


if __name__ == "__main__":
    unittest.main()
