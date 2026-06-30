from __future__ import annotations

from typing import Any

# backend 전용 읽기 repository. worker 산출물(final_signals + 조인/집계)을 base 테이블이
# 아니라 api 읽기 view 로만 조회한다(읽기 계약). is_current/is_published 필터와 stocks/
# analysis_results 조인, agent_results/signal_events JSONB 집계는 view 안에 내장돼 있고
# (20260625_1343_api_schema_read_contract), 여기서는 요청별 WHERE/ORDER/LIMIT 만 둔다.
# backend 롤은 base final_signals 권한이 없고 api.* SELECT 만 가진다.


class SignalRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def get_current_by_ticker(self, ticker: str) -> Any:
        # 사용자 노출 리포트는 통합 신호(run_key='AGGREGATED' — 6소스 score_breakdown 합본)를
        # 우선한다. 동일 종목에 per-source 런(DART/PRICE/PATENT/…)도 is_current/is_published 로
        # 함께 노출되는데, 이들은 자기 소스 외 score_breakdown 이 비어 있어(=데이터 수집 전) 그대로
        # 고르면 빈 카드만 나온다. AGGREGATED 가 없을 때만 최신 발행본으로 폴백한다.
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM api.signals_current
            WHERE ticker = $1
            ORDER BY (run_key = 'AGGREGATED') DESC,
                     published_at DESC NULLS LAST,
                     created_at DESC
            LIMIT 1
            """,
            ticker.strip(),
        )

    async def get_detail_by_id(self, signal_id: int) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM api.signal_detail
            WHERE id = $1
            LIMIT 1
            """,
            signal_id,
        )

    async def list_current_published(self, limit: int = 50) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT *
            FROM api.signals_current
            ORDER BY published_at DESC NULLS LAST,
                     created_at DESC
            LIMIT $1
            """,
            limit,
        )

    async def list_current_by_stock_ids(self, stock_ids: list[int]) -> list[Any]:
        if not stock_ids:
            return []
        return await self._connection.fetch(
            """
            SELECT *
            FROM api.signals_current
            WHERE stock_id = ANY($1::BIGINT[])
            ORDER BY stock_id ASC,
                     published_at DESC NULLS LAST,
                     created_at DESC
            """,
            stock_ids,
        )
