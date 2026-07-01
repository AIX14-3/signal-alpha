"""collection_schedules 리포지토리 (수집 스케줄 제어 평면, 백엔드 DB 소유 테이블).

어드민(main-server, signal_backend)은 이 테이블로 스케줄 config 를 읽고/쓰고, 워커측
스케줄러(run_scheduler_instance)는 발행용 백엔드 연결로 같은 행을 폴링해 발화 여부를
판단하고 실행 상태를 기록한다. 양쪽이 같은 SQL 을 쓰도록 한 곳에 모은다.

backend 는 ``signal_alpha_data_access.backend`` 에서 import(CI 가드). 워커 스크립트는
``signal_alpha_data_access.repositories.collection_schedules`` 에서 직접 import 한다.

jsonb 컬럼(targets/price_modes)은 asyncpg 기본이 str 이므로, 쓰기 시 json.dumps + ::jsonb
캐스팅하고 읽기는 ``parse_schedule_row`` 로 디코드한다.
"""

from __future__ import annotations

import json
from typing import Any


def parse_schedule_row(row: Any) -> dict[str, Any] | None:
    """asyncpg Record → dict. targets/price_modes jsonb 문자열을 list 로 디코드."""
    if row is None:
        return None
    data = dict(row)
    for key in ("targets", "price_modes"):
        value = data.get(key)
        if isinstance(value, str):
            try:
                data[key] = json.loads(value)
            except (ValueError, TypeError):
                data[key] = []
    return data


class CollectionScheduleRepository:
    """collection_schedules base 테이블 DML. 어드민·워커 스케줄러 공용."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def list_all(self) -> list[Any]:
        return await self._connection.fetch(
            "SELECT * FROM collection_schedules ORDER BY id ASC"
        )

    async def get_by_id(self, schedule_id: int) -> Any:
        return await self._connection.fetchrow(
            "SELECT * FROM collection_schedules WHERE id = $1", schedule_id
        )

    async def get_by_name(self, name: str) -> Any:
        return await self._connection.fetchrow(
            "SELECT * FROM collection_schedules WHERE name = $1", name
        )

    async def get_primary(self) -> Any:
        """기본(가장 오래된) 스케줄 1행 — 단일 스케줄 운용 시 진입점."""
        return await self._connection.fetchrow(
            "SELECT * FROM collection_schedules ORDER BY id ASC LIMIT 1"
        )

    async def update_config(
        self,
        *,
        schedule_id: int,
        enabled: bool | None = None,
        run_at_local: Any | None = None,
        timezone: str | None = None,
        targets: list[str] | None = None,
        dart_limit: int | None = None,
        price_modes: list[str] | None = None,
        frequency_minutes: int | None = None,
        active_from_local: Any | None = None,
        active_until_local: Any | None = None,
        updated_by: str | None = None,
    ) -> Any:
        """보낸 필드만 갱신(COALESCE). targets/price_modes 는 ::jsonb 캐스팅."""
        targets_json = json.dumps(targets) if targets is not None else None
        price_modes_json = json.dumps(price_modes) if price_modes is not None else None
        return await self._connection.fetchrow(
            """
            UPDATE collection_schedules
            SET enabled      = COALESCE($2, enabled),
                run_at_local = COALESCE($3, run_at_local),
                timezone     = COALESCE($4, timezone),
                targets      = COALESCE($5::jsonb, targets),
                dart_limit   = COALESCE($6, dart_limit),
                price_modes  = COALESCE($7::jsonb, price_modes),
                frequency_minutes = COALESCE($8, frequency_minutes),
                active_from_local = COALESCE($9, active_from_local),
                active_until_local = COALESCE($10, active_until_local),
                updated_by   = COALESCE($11, updated_by),
                updated_at   = NOW()
            WHERE id = $1
            RETURNING *
            """,
            schedule_id,
            enabled,
            run_at_local,
            timezone,
            targets_json,
            dart_limit,
            price_modes_json,
            frequency_minutes,
            active_from_local,
            active_until_local,
            updated_by,
        )

    async def request_manual_trigger(
        self, *, schedule_id: int, updated_by: str | None = None
    ) -> Any:
        """'지금 실행' — manual_trigger_requested_at=NOW(). 스케줄러가 다음 폴링에 발화."""
        return await self._connection.fetchrow(
            """
            UPDATE collection_schedules
            SET manual_trigger_requested_at = NOW(),
                updated_by = COALESCE($2, updated_by),
                updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            schedule_id,
            updated_by,
        )

    async def record_run(
        self,
        *,
        schedule_id: int,
        last_run_at: Any,
        last_status: str,
        last_detail: Any | None,
        next_run_at: Any,
    ) -> Any:
        """스케줄러가 발화 후 실행 상태를 기록(last_detail 은 ::jsonb 캐스팅)."""
        detail_json = json.dumps(last_detail) if last_detail is not None else None
        return await self._connection.fetchrow(
            """
            UPDATE collection_schedules
            SET last_run_at  = $2,
                last_status  = $3,
                last_detail  = $4::jsonb,
                next_run_at  = $5
            WHERE id = $1
            RETURNING *
            """,
            schedule_id,
            last_run_at,
            last_status,
            detail_json,
            next_run_at,
        )

    async def start_run(
        self,
        *,
        schedule_id: int,
        schedule_name: str,
        trigger_reason: str,
        targets: list[str],
    ) -> Any:
        """Insert a scheduler execution history row before firing targets."""
        targets_json = json.dumps(targets)
        return await self._connection.fetchrow(
            """
            INSERT INTO collection_schedule_runs (
                schedule_id,
                schedule_name,
                trigger_reason,
                targets
            )
            VALUES ($1, $2, $3, $4::jsonb)
            RETURNING *
            """,
            schedule_id,
            schedule_name,
            trigger_reason,
            targets_json,
        )

    async def finish_run(
        self,
        *,
        run_id: int,
        status: str,
        detail: Any | None,
    ) -> Any:
        """Mark a scheduler execution history row complete."""
        detail_json = json.dumps(detail) if detail is not None else None
        return await self._connection.fetchrow(
            """
            UPDATE collection_schedule_runs
            SET status = $2,
                detail = $3::jsonb,
                finished_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            run_id,
            status,
            detail_json,
        )

    async def list_recent_runs(self, *, schedule_id: int, limit: int = 20) -> list[Any]:
        """List recent scheduler execution history rows for one schedule."""
        return await self._connection.fetch(
            """
            SELECT *
            FROM collection_schedule_runs
            WHERE schedule_id = $1
            ORDER BY started_at DESC
            LIMIT $2
            """,
            schedule_id,
            limit,
        )
