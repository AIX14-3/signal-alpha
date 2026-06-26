from __future__ import annotations

from typing import Any


class EventStudyRepository:
    """event_study_panel — L6 forward-return 라벨 패널 접근.

    signal_events 를 앵커로 asof+1 부터의 forward/abnormal return 을 멱등 적재하고,
    base 모델·메타러너 학습(라벨) + lift 채택 게이트가 기간 범위로 조회한다.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_panel_row(
        self,
        *,
        signal_event_id: int,
        stock_id: int,
        asof_date: Any,
        fwd_return_1d: float | None = None,
        fwd_return_5d: float | None = None,
        fwd_return_20d: float | None = None,
        abnormal_return_20d: float | None = None,
        universe_snapshot: str = "kospi20_seed",
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO event_study_panel (
                signal_event_id, stock_id, asof_date,
                fwd_return_1d, fwd_return_5d, fwd_return_20d,
                abnormal_return_20d, universe_snapshot
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (signal_event_id, asof_date)
            DO UPDATE SET
                stock_id = EXCLUDED.stock_id,
                fwd_return_1d = EXCLUDED.fwd_return_1d,
                fwd_return_5d = EXCLUDED.fwd_return_5d,
                fwd_return_20d = EXCLUDED.fwd_return_20d,
                abnormal_return_20d = EXCLUDED.abnormal_return_20d,
                universe_snapshot = EXCLUDED.universe_snapshot
            RETURNING *
            """,
            signal_event_id,
            stock_id,
            asof_date,
            fwd_return_1d,
            fwd_return_5d,
            fwd_return_20d,
            abnormal_return_20d,
            universe_snapshot,
        )

    async def list_for_training(
        self,
        *,
        asof_from: Any,
        asof_to: Any,
        universe_snapshot: str | None = None,
    ) -> list[Any]:
        """학습 라벨 조회 — 기간 [asof_from, asof_to] 내 패널 행(시간순).

        ``universe_snapshot`` 지정 시 해당 유니버스로 한정(생존편향 차단).
        """
        return await self._connection.fetch(
            """
            SELECT *
            FROM event_study_panel
            WHERE asof_date BETWEEN $1 AND $2
              AND ($3::VARCHAR IS NULL OR universe_snapshot = $3)
            ORDER BY asof_date ASC, id ASC
            """,
            asof_from,
            asof_to,
            universe_snapshot,
        )

    async def list_by_stock(self, *, stock_id: int, limit: int = 100) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT *
            FROM event_study_panel
            WHERE stock_id = $1
            ORDER BY asof_date DESC, id DESC
            LIMIT $2
            """,
            stock_id,
            limit,
        )
