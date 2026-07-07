"""매매 부검 PIT 관측신호 오버레이 리포지토리 (backend DB).

워커가 수집 DB(dart_ownership 등)에서 유저 거래 구간의 관측 가능 신호를 멱등 적재하고,
backend(부검 라우트)가 종목별로 읽어 라운드트립 보유 구간에 겹친다. signal_date=known_at(PIT).

asyncpg 스타일: 키워드 전용 인자, $n 위치 파라미터.
"""

from __future__ import annotations

from typing import Any

_OVERLAY_COLUMNS = "id, stock_id, ticker, signal_date, kind, source_ref, detail, created_at"


class UserTradeSignalOverlayRepository:
    """PIT 신호 오버레이 적재(워커) + 종목별 조회(backend)."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_overlay(
        self,
        *,
        user_id: int,
        stock_id: int,
        ticker: str,
        signal_date: Any,
        kind: str,
        source_ref: str,
        detail: str,
    ) -> None:
        """멱등 적재 — (user, stock, date, kind, source_ref) 자연키.

        source_ref(공시 rcept_no:line_seq)로 같은 날 여러 내부자 공시를 각각 보존한다.
        공시 1건의 detail 은 불변(정정은 새 rcept_no)이라 재적재 시 DO NOTHING — 워커에
        UPDATE 권한이 필요 없다(grant=SELECT/INSERT).
        """
        await self._connection.execute(
            """
            INSERT INTO user_trade_signal_overlays
                (user_id, stock_id, ticker, signal_date, kind, source_ref, detail)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            ON CONFLICT (user_id, stock_id, signal_date, kind, source_ref) DO NOTHING
            """,
            user_id,
            stock_id,
            ticker,
            signal_date,
            kind,
            source_ref,
            detail,
        )

    async def list_by_stock(self, *, user_id: int, stock_id: int) -> list[Any]:
        """부검 라우트용 — 종목의 관측신호 전체(시간순). 라운드트립 구간 필터는 호출부."""
        return await self._connection.fetch(
            f"SELECT {_OVERLAY_COLUMNS} FROM user_trade_signal_overlays "
            "WHERE user_id = $1 AND stock_id = $2 ORDER BY signal_date",
            user_id,
            stock_id,
        )

    async def traded_stock_ranges(self, *, user_id: int | None = None) -> list[Any]:
        """오버레이 대상 — 매핑된 종목별 거래 구간(min/max filled_at). 워커가 순회한다."""
        if user_id is not None:
            return await self._connection.fetch(
                "SELECT user_id, stock_id, ticker, min(filled_at)::date AS start_date, "
                "max(filled_at)::date AS end_date FROM user_trade_fills "
                "WHERE stock_id IS NOT NULL AND user_id = $1 "
                "GROUP BY user_id, stock_id, ticker",
                user_id,
            )
        return await self._connection.fetch(
            "SELECT user_id, stock_id, ticker, min(filled_at)::date AS start_date, "
            "max(filled_at)::date AS end_date FROM user_trade_fills "
            "WHERE stock_id IS NOT NULL GROUP BY user_id, stock_id, ticker"
        )
