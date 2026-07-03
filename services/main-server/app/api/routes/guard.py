from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.routes.admin_auth import get_current_admin
from app.core.database import get_database_pool

# 지정학 리스크 Kill-Switch — 차단 상태의 단일 진실원천은 guard_site_status 싱글턴 1행.
# 공개 status 는 프론트가 폴링해 노출만 게이트하고(fail-open 은 프론트 책임),
# 생성/발행 파이프라인은 건드리지 않는다(무조건 발행 결정 유지).
router = APIRouter(prefix="/api/guard", tags=["guard"])
# 관리자 세션 쿠키(sa_admin)는 /api/admin 경로 스코프이므로 관리자 엔드포인트는
# /api/admin/guard/* 에 둔다(다른 경로면 브라우저가 쿠키를 보내지 않는다).
admin_router = APIRouter(prefix="/api/admin/guard", tags=["guard-admin"])

_AUDIT_LIMIT = 20
_RECOMMENDATION_LIMIT = 50

GuardScope = Literal["report_generation", "report_view", "whole_site"]


class GuardStatusUpdateRequest(BaseModel):
    status: Literal["ok", "blocked"]
    scope: GuardScope
    mode: Literal["manual", "advisory", "auto"]
    reason: str | None = None
    resume_at: datetime | None = None


@router.get("/status")
async def get_guard_status(pool: Any = Depends(get_database_pool)) -> dict[str, Any]:
    """공개 차단 상태 — 비로그인 포함 누구나 폴링. 항상 200."""
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT status, scope, reason, resume_at FROM guard_site_status WHERE id = 1"
        )
    # 싱글턴 행은 마이그레이션이 시드하지만, 유실 시에도 공개 API 는 정상(ok)으로 응답한다.
    if row is None:
        return {"status": "ok", "scope": "report_generation", "reason": None, "resume_at": None}
    return {
        "status": row["status"],
        "scope": row["scope"],
        "reason": row["reason"],
        "resume_at": row["resume_at"],
    }


@admin_router.get("/status")
async def get_guard_admin_status(
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        row = await connection.fetchrow("SELECT * FROM guard_site_status WHERE id = 1")
        audit_rows = await connection.fetch(
            "SELECT action, scope, reason, actor, created_at FROM guard_status_audit"
            " ORDER BY created_at DESC LIMIT $1",
            _AUDIT_LIMIT,
        )
    if row is None:
        raise _api_error(500, "GUARD_STATUS_MISSING", "차단 상태 행이 없습니다. 마이그레이션을 확인하세요.")
    return {"status": dict(row), "audit": [dict(item) for item in audit_rows]}


@admin_router.put("/status")
async def update_guard_status(
    payload: GuardStatusUpdateRequest,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    actor = _admin_actor(admin)
    async with pool.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                "UPDATE guard_site_status"
                " SET status = $1, scope = $2, mode = $3, reason = $4, resume_at = $5,"
                "     triggered_by = $6, updated_at = now()"
                " WHERE id = 1 RETURNING *",
                payload.status,
                payload.scope,
                payload.mode,
                payload.reason,
                payload.resume_at,
                actor,
            )
            await _record_audit(
                connection,
                action="block" if payload.status == "blocked" else "unblock",
                scope=payload.scope,
                reason=payload.reason,
                actor=actor,
            )
    if row is None:
        raise _api_error(500, "GUARD_STATUS_MISSING", "차단 상태 행이 없습니다. 마이그레이션을 확인하세요.")
    return {"status": dict(row)}


@admin_router.get("/recommendations")
async def list_guard_recommendations(
    status: Literal["pending", "approved", "rejected"] = "pending",
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT r.id, r.news_event_id, r.suggested_scope, r.severity, r.reason,"
            "       r.status, r.decided_by, r.decided_at, r.created_at,"
            "       n.title AS news_title, n.url AS news_url"
            " FROM guard_recommendations r"
            " LEFT JOIN guard_news_events n ON n.id = r.news_event_id"
            " WHERE r.status = $1"
            " ORDER BY r.created_at DESC LIMIT $2",
            status,
            _RECOMMENDATION_LIMIT,
        )
    items = [dict(row) for row in rows]
    return {"count": len(items), "items": items}


@admin_router.post("/recommendations/{recommendation_id}/approve")
async def approve_guard_recommendation(
    recommendation_id: int,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """제안 승인 = 제안된 scope 로 즉시 차단 적용(트랜잭션)."""
    actor = _admin_actor(admin)
    async with pool.acquire() as connection:
        async with connection.transaction():
            recommendation = await _decide_recommendation(
                connection, recommendation_id, decision="approved", actor=actor
            )
            status_row = await connection.fetchrow(
                "UPDATE guard_site_status"
                " SET status = 'blocked', scope = $1, reason = $2,"
                "     triggered_by = $3, updated_at = now()"
                " WHERE id = 1 RETURNING *",
                recommendation["suggested_scope"],
                recommendation["reason"],
                actor,
            )
            await _record_audit(
                connection,
                action="block",
                scope=recommendation["suggested_scope"],
                reason=recommendation["reason"],
                actor=actor,
            )
    if status_row is None:
        raise _api_error(500, "GUARD_STATUS_MISSING", "차단 상태 행이 없습니다. 마이그레이션을 확인하세요.")
    return {"recommendation": recommendation, "status": dict(status_row)}


@admin_router.post("/recommendations/{recommendation_id}/reject")
async def reject_guard_recommendation(
    recommendation_id: int,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    actor = _admin_actor(admin)
    async with pool.acquire() as connection:
        async with connection.transaction():
            recommendation = await _decide_recommendation(
                connection, recommendation_id, decision="rejected", actor=actor
            )
    return {"recommendation": recommendation}


async def _decide_recommendation(
    connection: Any, recommendation_id: int, *, decision: str, actor: str
) -> dict[str, Any]:
    # pending 조건을 UPDATE 에 포함해 동시 승인/거절 경합을 원자적으로 차단한다.
    row = await connection.fetchrow(
        "UPDATE guard_recommendations"
        " SET status = $2, decided_by = $3, decided_at = now()"
        " WHERE id = $1 AND status = 'pending'"
        " RETURNING *",
        recommendation_id,
        decision,
        actor,
    )
    if row is None:
        raise _api_error(404, "RECOMMENDATION_NOT_PENDING", "대기 중인 제안이 아닙니다.")
    return dict(row)


async def _record_audit(
    connection: Any, *, action: str, scope: str | None, reason: str | None, actor: str
) -> None:
    await connection.execute(
        "INSERT INTO guard_status_audit (action, scope, reason, actor) VALUES ($1, $2, $3, $4)",
        action,
        scope,
        reason,
        actor,
    )


def _admin_actor(admin: dict[str, Any]) -> str:
    return f"admin:{admin.get('admin_email') or admin.get('admin_id')}"


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
