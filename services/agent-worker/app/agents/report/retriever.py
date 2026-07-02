"""RAG retrieval over ``report_chunks`` (#709) for the Report source agent.

Cosine Top-K vector search: embeds the query with the injected embedder, then orders
by the pgvector ``<=>`` cosine-distance operator (matching the HNSW ``vector_cosine_ops``
index). ``report_chunks.stock_id`` (denormalized, 1B) scopes results directly via a btree
filter, so the planner grabs the stock's (few) chunks and sorts them exactly — exact
recall, independent of HNSW post-filtering.

Returns chunk text plus provenance (broker, publish_date, raw_document_id) — never a
score/direction. This is a tool the agent consumes; it does not itself judge anything.
"""
from __future__ import annotations

from typing import Any

from app.clients.pgvector import to_pgvector

# 종목 후필터 리콜 확보용 HNSW 후보 폭(pgvector 기본 40). top_k(=5) 대비 넉넉하게.
_EF_SEARCH = 100

# pgvector 버전 감지 캐시(런타임 불변). None=미확인, True/False=iterative_scan 지원 여부.
# 1B 적용: report_chunks.stock_id 비정규화로 종목 필터가 exact 스캔이 돼 리콜은 이미 정확하다.
# ef_search/iterative_scan(1A)은 플래너가 혹시 HNSW 를 고르는 경우의 안전망으로 유지한다(벨트+멜빵).
_ITERATIVE_SCAN: bool | None = None


def _version_ge(version: object, minimum: tuple[int, int]) -> bool:
    """'0.8.2' 같은 extversion 이 minimum(major,minor) 이상인지."""
    if not version:
        return False
    try:
        parts = tuple(int(p) for p in str(version).split(".")[:2])
    except ValueError:
        return False
    return parts >= minimum


async def _supports_iterative_scan(connection: object) -> bool:
    """대상 DB pgvector 가 iterative index scan(≥0.8)을 지원하는지 — 1회 감지 후 캐시.

    미지원(구버전)이면 iterative_scan GUC 자체가 없어 SET 이 에러나므로, 켜기 전에 반드시 확인한다.
    """
    global _ITERATIVE_SCAN
    if _ITERATIVE_SCAN is None:
        try:
            ext = await connection.fetchval(  # type: ignore[attr-defined]
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            )
        except Exception:  # noqa: BLE001 — 감지 실패 시 보수적으로 미지원 취급(ef_search 만)
            ext = None
        _ITERATIVE_SCAN = _version_ge(ext, (0, 8))
    return _ITERATIVE_SCAN


async def retrieve(
    connection: Any,
    *,
    stock_id: int,
    query: str,
    embedder: Any,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Return the ``top_k`` most similar report chunks for ``stock_id`` with provenance."""
    qvec = to_pgvector(await embedder.embed(query))
    # 1B: rc.stock_id 직접 필터 → 플래너가 종목 청크(소량)만 골라 exact 정렬 = 리콜 정확.
    # ef_search + iterative_scan(≥0.8)은 안전망으로 유지(플래너가 혹시 HNSW 를 고를 때 대비).
    async with connection.transaction():
        await connection.execute(f"SET LOCAL hnsw.ef_search = {_EF_SEARCH}")
        if await _supports_iterative_scan(connection):
            await connection.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
        rows = await connection.fetch(
            """
            SELECT rc.chunk_text,
                   rc.report_raw_detail_id AS raw_document_id,
                   rrd.securities_firm     AS broker,
                   rrd.publish_date
            FROM report_chunks rc
            JOIN report_raw_details rrd ON rrd.raw_document_id = rc.report_raw_detail_id
            WHERE rc.stock_id = $2
            ORDER BY rc.embedding <=> $1::vector
            LIMIT $3
            """,
            qvec,
            stock_id,
            top_k,
        )
    return [dict(row) for row in rows]
