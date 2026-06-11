from __future__ import annotations

from typing import Any, Iterable


class ReportChunkRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def list_embedding_pending(self, limit: int = 100) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                id,
                raw_document_id,
                stock_id,
                chunk_index,
                chunk_text,
                token_count,
                created_at
            FROM report_chunks
            WHERE embedding IS NULL
            ORDER BY created_at ASC, id ASC
            LIMIT $1
            """,
            limit,
        )

    async def update_embedding(self, *, chunk_id: int, embedding: Iterable[float]) -> None:
        await self._connection.execute(
            """
            UPDATE report_chunks
            SET embedding = $2::vector
            WHERE id = $1
            """,
            chunk_id,
            _format_pgvector(embedding),
        )

    async def search_similar(
        self,
        *,
        stock_id: int,
        query_embedding: Iterable[float],
        limit: int = 5,
    ) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                id,
                raw_document_id,
                stock_id,
                chunk_index,
                chunk_text,
                token_count,
                embedding <=> $2::vector AS distance
            FROM report_chunks
            WHERE stock_id = $1
              AND embedding IS NOT NULL
            ORDER BY embedding <=> $2::vector
            LIMIT $3
            """,
            stock_id,
            _format_pgvector(query_embedding),
            limit,
        )


def _format_pgvector(values: Iterable[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"
