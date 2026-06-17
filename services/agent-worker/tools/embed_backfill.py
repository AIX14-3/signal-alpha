"""report 임베딩 백필.

이미 파싱(parsing_status='success')됐지만 아직 report_chunks가 없는 문서에 대해
embed_report 태스크를 일괄 등록한다. 실제 임베딩(전문 추출→청킹→BGE-M3→적재)은
worker의 ReportEmbedTaskHandler가 싱글톤 모델로 처리한다(스크립트는 모델을 로딩하지 않음).

사용:
    uv run --package signal-alpha-agent-worker python -m tools.embed_backfill
    uv run --package signal-alpha-agent-worker python -m tools.embed_backfill --stock 005930 --limit 500

전제: DATABASE_URL 설정. worker 루프가 떠 있어야 등록된 태스크가 처리된다.
"""
from __future__ import annotations

import argparse
import asyncio

from app.core.config import get_settings
from app.orchestrator.queue.task_types import EMBED_REPORT


async def enqueue_backfill(*, limit: int, stock: str | None) -> int:
    from signal_alpha_data_access import DatabaseSettings, create_pool
    from signal_alpha_data_access.repositories import ProcessingQueueRepository

    settings = get_settings()
    pool = await create_pool(DatabaseSettings(database_url=settings.database_url))
    try:
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT rd.id AS raw_document_id, rd.stock_id
                FROM raw_documents rd
                JOIN report_raw_details rrd ON rrd.raw_document_id = rd.id
                JOIN stocks s               ON s.id = rd.stock_id
                WHERE rrd.parsing_status = 'success'
                  AND ($1::varchar IS NULL OR s.ticker = $1)
                  AND NOT EXISTS (
                      SELECT 1 FROM report_chunks rc
                      WHERE rc.raw_document_id = rd.id
                  )
                ORDER BY rd.id
                LIMIT $2
                """,
                stock,
                limit,
            )

            queue = ProcessingQueueRepository(connection)
            enqueued = 0
            for r in rows:
                await queue.enqueue(
                    stock_id=int(r["stock_id"]),
                    task_type=EMBED_REPORT,
                    priority="batch",
                    source_raw_ids=[int(r["raw_document_id"])],
                    task_context={"raw_document_id": int(r["raw_document_id"])},
                    dedupe=True,
                )
                enqueued += 1
            return enqueued
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="report embed_report 백필 등록")
    parser.add_argument("--limit", type=int, default=1000, help="등록할 최대 문서 수")
    parser.add_argument("--stock", default=None, help="종목코드 필터 (예: 005930)")
    args = parser.parse_args()

    enqueued = asyncio.run(enqueue_backfill(limit=args.limit, stock=args.stock))
    print(f"enqueued embed_report tasks: {enqueued}")


if __name__ == "__main__":
    main()
