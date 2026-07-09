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

    # ``api.signals_current`` 는 이름과 달리 종목당 한 행이 아니다. is_current 는
    # (stock_id, signal_date, run_key) 단위로만 유일하므로 **과거 signal_date 의 행도 계속
    # is_current=TRUE** 로 남는다(analysis.py 상단 주석). 목록 API 가 이 view 를 그대로 읽으면
    # 한 종목의 수십~수백 일치 이력이 딸려 와, 호출부의 종목별 그룹핑이 날짜를 가로질러
    # 점수를 평균내 버린다(=어느 날짜의 값도 아닌 수).
    #
    # 그래서 목록 조회는 여기서 **(종목, run_key) 별 최신 1행**으로 접어 준다. 최신 기준은
    # signal_date(신호가 가리키는 날) → published_at → created_at 순. 종목당 행 수는
    # run_key 개수(AGGREGATED + 소스별)로 묶인다.
    _LATEST_PER_SOURCE = """
        SELECT DISTINCT ON (stock_id, run_key) *
        FROM api.signals_current
        {where}
        ORDER BY stock_id,
                 run_key,
                 signal_date DESC,
                 published_at DESC NULLS LAST,
                 created_at DESC
    """

    async def list_current_published(self, limit: int = 50) -> list[Any]:
        """최근 발행된 상위 ``limit`` **종목**의 현재 신호(종목×run_key 최신 1행).

        ``limit`` 은 행이 아니라 **종목** 수다. 행에 걸면 한 종목의 이력이 창을 다 먹어
        나머지 종목이 잘려 나간다(브리핑이 종목 몇 개만 보이던 원인).
        """
        return await self._connection.fetch(
            f"""
            WITH latest AS ({self._LATEST_PER_SOURCE.format(where="")}),
            recent_stocks AS (
                SELECT stock_id
                FROM latest
                GROUP BY stock_id
                ORDER BY MAX(published_at) DESC NULLS LAST, MAX(created_at) DESC
                LIMIT $1
            )
            SELECT latest.*
            FROM latest
            INNER JOIN recent_stocks USING (stock_id)
            ORDER BY latest.published_at DESC NULLS LAST,
                     latest.created_at DESC
            """,
            limit,
        )

    async def list_current_by_stock_ids(self, stock_ids: list[int]) -> list[Any]:
        if not stock_ids:
            return []
        return await self._connection.fetch(
            f"""
            WITH latest AS (
                {self._LATEST_PER_SOURCE.format(where="WHERE stock_id = ANY($1::BIGINT[])")}
            )
            SELECT *
            FROM latest
            ORDER BY stock_id ASC,
                     published_at DESC NULLS LAST,
                     created_at DESC
            """,
            stock_ids,
        )
