"""backend 읽기 계약 repository.

worker가 적재하는 데이터(stocks, processing_queue 등)를 backend가 직접 base 테이블로
읽지 않고 `api` 스키마의 읽기전용 view 로만 조회한다(20260625_1343_api_schema_read_contract).
backend 롤은 base 테이블 권한이 없고 api.* SELECT 권한만 가지므로, backend 코드는 반드시
이 클래스들을 통해 view 를 조회해야 한다.

worker 가 쓰는 base 테이블용 repository(stocks.StockRepository.ensure_stock,
processing_queue.ProcessingQueueRepository 전체)와 클래스명은 같지만, 여기 정의는
읽기 메서드만 두고 FROM 대상이 api.* view 다. backend 는 `signal_alpha_data_access.backend`
에서 이 클래스들을 import 한다.
"""

from __future__ import annotations

from typing import Any


class StockRepository:
    """종목 마스터 읽기 (api.stocks). backend 전용. 쓰기(ensure_stock)는 worker repo."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def get_by_ticker(self, ticker: str) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT id, ticker, name, market, sector, is_active, created_at, updated_at
            FROM api.stocks
            WHERE ticker = $1
            """,
            ticker.strip(),
        )

    async def list_active(self, limit: int = 100) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT id, ticker, name, market, sector, is_active, created_at, updated_at
            FROM api.stocks
            WHERE is_active = TRUE
            ORDER BY market ASC, ticker ASC
            LIMIT $1
            """,
            limit,
        )

    async def search_active(self, query: str, limit: int = 20) -> list[Any]:
        pattern = f"%{query.strip()}%"
        return await self._connection.fetch(
            """
            SELECT id, ticker, name, market, sector, is_active, created_at, updated_at
            FROM api.stocks
            WHERE is_active = TRUE
              AND (
                  ticker ILIKE $1
                  OR name ILIKE $1
              )
            ORDER BY market ASC, ticker ASC
            LIMIT $2
            """,
            pattern,
            limit,
        )


class StockNewsRepository:
    """종목별 뉴스 읽기 (api.stock_news). backend 전용. 적재(INSERT)는 worker 뉴스 데몬."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def list_by_ticker(self, ticker: str, limit: int = 20) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT stock_code, title, summary, url, press, source, published_at, collected_at
            FROM api.stock_news
            WHERE stock_code = $1
            ORDER BY published_at DESC NULLS LAST, collected_at DESC
            LIMIT $2
            """,
            ticker.strip(),
            limit,
        )

    async def count_by_ticker(self, ticker: str) -> int:
        value = await self._connection.fetchval(
            "SELECT COUNT(*) FROM api.stock_news WHERE stock_code = $1",
            ticker.strip(),
        )
        return int(value or 0)

    async def get_digest_by_ticker(self, ticker: str) -> Any | None:
        """종목 뉴스 다이제스트(LLM 종합 한 줄) — 없으면 None. 적재는 worker 뉴스 데몬."""
        return await self._connection.fetchrow(
            """
            SELECT stock_code, digest_text, model, article_count, generated_at
            FROM api.stock_news_digest
            WHERE stock_code = $1
            """,
            ticker.strip(),
        )

    async def list_recent(self, *, limit: int = 30) -> list[Any]:
        """전역 최신 뉴스(종목 무관) + 종목명 조인. 홈 2-pane 좌측 '뉴스 피드'용(공개).

        api.stock_news 는 ticker(stock_code)만 있어 표시용 종목명은 api.stocks 를 LEFT JOIN
        해 채운다(미매핑/상장폐지 종목은 stock_name NULL 로 보존).
        """
        return await self._connection.fetch(
            """
            SELECT n.stock_code, s.name AS stock_name,
                   n.title, n.summary, n.url, n.press, n.source, n.published_at
            FROM api.stock_news n
            LEFT JOIN api.stocks s ON s.ticker = n.stock_code
            ORDER BY n.published_at DESC NULLS LAST, n.collected_at DESC
            LIMIT $1
            """,
            limit,
        )

    async def summary(self, *, recent_hours: int = 24) -> dict[str, Any]:
        """전역 뉴스 집계 — 총 기사수·뉴스 보유 종목수·최근 N시간 기사수·최신 수집시각.

        토스식 '뉴스 N건을 분석한 시그널' 헤더용(공개). 종목 무관 전체 집계라 파라미터는
        최근 윈도우(시간)만 받는다.
        """
        row = await self._connection.fetchrow(
            """
            SELECT
                COUNT(*) AS total_articles,
                COUNT(DISTINCT stock_code) AS stock_count,
                MAX(collected_at) AS latest_collected_at,
                COUNT(*) FILTER (
                    WHERE collected_at >= now() - make_interval(hours => $1)
                ) AS recent_articles
            FROM api.stock_news
            """,
            recent_hours,
        )
        return dict(row) if row is not None else {}


class ProcessingQueueRepository:
    """분석 파이프라인 상태 읽기 (api.analysis_pipeline_status). backend 전용.

    worker 쪽 enqueue/claim/mark 등 쓰기 메서드는 base 테이블용
    signal_alpha_data_access.worker 의 ProcessingQueueRepository 에 있다. backend 는
    analytics 폴링에서 list_tasks 만 사용한다.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def list_tasks(
        self,
        *,
        stock_code: str | None = None,
        task_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT *
            FROM api.analysis_pipeline_status
            WHERE ($1::VARCHAR IS NULL OR stock_code = $1)
              AND ($2::VARCHAR IS NULL OR task_type = $2)
              AND ($3::VARCHAR IS NULL OR status = $3)
            ORDER BY created_at DESC, id DESC
            LIMIT $4
            """,
            stock_code,
            task_type,
            status,
            limit,
        )
