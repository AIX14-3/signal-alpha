"""RAG retrieval over ``report_chunks`` (#709) for the Report source agent.

Cosine Top-K vector search: embeds the query with the injected embedder, then orders
by the pgvector ``<=>`` cosine-distance operator (matching the HNSW ``vector_cosine_ops``
index). ``report_chunks`` carries no ``stock_id``, so results are scoped by joining
``report_raw_details.raw_document_id`` (== ``raw_documents.id``) to ``raw_documents.stock_id``.

Returns chunk text plus provenance (broker, publish_date, raw_document_id) — never a
score/direction. This is a tool the agent consumes; it does not itself judge anything.
"""
from __future__ import annotations

from typing import Any

from app.clients.pgvector import to_pgvector

# 종목 후필터 리콜 확보용 HNSW 후보 폭(pgvector 기본 40). top_k(=5) 대비 넉넉하게.
_EF_SEARCH = 100


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
    # HNSW 는 전역 최근접 후보(ef_search 개)를 먼저 뽑고 stock_id 를 후필터하므로, 코퍼스가 커지면
    # 대상 종목 청크가 후보에서 밀려 top_k 미만/0 을 돌려줄 수 있다. ef_search 를 넉넉히(트랜잭션
    # 로컬로만) 올려 종목 필터 리콜을 확보한다.
    async with connection.transaction():
        await connection.execute(f"SET LOCAL hnsw.ef_search = {_EF_SEARCH}")
        rows = await connection.fetch(
            """
            SELECT rc.chunk_text,
                   rc.report_raw_detail_id AS raw_document_id,
                   rrd.securities_firm     AS broker,
                   rrd.publish_date
            FROM report_chunks rc
            JOIN raw_documents rd       ON rd.id = rc.report_raw_detail_id
            JOIN report_raw_details rrd ON rrd.raw_document_id = rc.report_raw_detail_id
            WHERE rd.stock_id = $2
            ORDER BY rc.embedding <=> $1::vector
            LIMIT $3
            """,
            qvec,
            stock_id,
            top_k,
        )
    return [dict(row) for row in rows]
