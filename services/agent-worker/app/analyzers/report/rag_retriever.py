"""Report RAG 검색 (canonical report_chunks, stock_id 필터 Top-K).

embed_report가 적재한 canonical `report_chunks` 에서 종목별로 코사인 유사도 Top-K 청크를
회수한다. **적재와 동일한 EmbeddingProvider** 로 질의를 임베딩해야 거리(<=>)가 유의미하다.

legacy `analyzers/report/analyzer.py` 의 `_rag_search()`(stock_code 컬럼 가정)는 현재 DB
스키마에서 동작하지 않는다 → 이 모듈이 canonical 대체.
"""
from __future__ import annotations

from typing import Any

from app.embeddings.provider import EmbeddingProvider, get_embedding_provider, to_pgvector


async def retrieve(
    connection: Any,
    *,
    stock_id: int,
    query: str,
    top_k: int = 5,
    provider: EmbeddingProvider | None = None,
) -> list[dict[str, Any]]:
    """`stock_id` 로 격리된 청크에서 query와 가장 가까운 Top-K를 돌려준다.

    반환: [{chunk_text, raw_document_id, chunk_index, similarity}, ...] (similarity DESC)
    """
    provider = provider or get_embedding_provider()
    query_embedding = (await provider.embed([query]))[0]

    rows = await connection.fetch(
        """
        SELECT chunk_text,
               raw_document_id,
               chunk_index,
               1 - (embedding <=> $1::vector) AS similarity
        FROM report_chunks
        WHERE stock_id = $2
          AND embedding IS NOT NULL
        ORDER BY embedding <=> $1::vector
        LIMIT $3
        """,
        to_pgvector(query_embedding),
        stock_id,
        top_k,
    )
    return [dict(row) for row in rows]


class ReportRagRetriever:
    """connection·provider를 바인딩한 호출 가능한 retriever.

    Agent(Phase 4)에 주입해 `await retriever(stock_id, query, top_k=3)` 형태로 쓴다.
    Agent가 DB·임베딩을 직접 다루지 않게 하기 위한 경계.
    """

    def __init__(self, connection: Any, *, provider: EmbeddingProvider | None = None) -> None:
        self._connection = connection
        self._provider = provider

    async def __call__(
        self, stock_id: int, query: str, top_k: int = 5
    ) -> list[dict[str, Any]]:
        return await retrieve(
            self._connection,
            stock_id=stock_id,
            query=query,
            top_k=top_k,
            provider=self._provider,
        )
