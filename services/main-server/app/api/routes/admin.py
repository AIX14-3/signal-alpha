from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.routes.admin_auth import ADMIN_SESSION_HOURS, admin_error, get_current_admin
from app.core.database import get_database_pool
from app.core.security import create_refresh_token, verify_password
from signal_alpha_data_access.repositories import AdminRepository, UserBillingRepository


admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminLoginRequest(BaseModel):
    email: str
    password: str


class UpdateSubscriptionRequest(BaseModel):
    plan_type: str
    status: str | None = None
    expires_at: str | None = None


@admin_router.post("/login")
async def admin_login(
    payload: AdminLoginRequest,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    email = payload.email.strip().lower()
    async with pool.acquire() as connection:
        repository = AdminRepository(connection)
        admin_row = await repository.get_admin_by_email(email)
        admin = dict(admin_row) if admin_row is not None else None
        if admin is None or not verify_password(payload.password, admin.get("password_hash")):
            raise admin_error(401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 올바르지 않습니다.")
        session_token = create_refresh_token()
        expires_at = datetime.now(UTC) + timedelta(hours=ADMIN_SESSION_HOURS)
        await repository.create_session(
            admin_id=int(admin["id"]),
            session_token=session_token,
            expires_at=expires_at,
        )
        await repository.update_last_login(admin_id=int(admin["id"]))
    return {
        "session_token": session_token,
        "expires_at": expires_at.isoformat(),
        "admin": {"id": admin["id"], "email": admin["email"]},
    }


@admin_router.post("/logout")
async def admin_logout(
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, str]:
    async with pool.acquire() as connection:
        await AdminRepository(connection).delete_session(
            session_token=str(admin["session_token"])
        )
    return {"status": "ok"}


@admin_router.get("/users")
async def list_users(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None),
    _admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    query = q.strip() if q and q.strip() else None
    offset = (page - 1) * size
    async with pool.acquire() as connection:
        repository = AdminRepository(connection)
        total = await repository.count_users(query=query)
        rows = await repository.list_users_paginated(limit=size, offset=offset, query=query)
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [_user_row(dict(row)) for row in rows],
    }


@admin_router.get("/users/{user_id}")
async def get_user_detail(
    user_id: int,
    _admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        row = await AdminRepository(connection).get_user_details(user_id=user_id)
    if row is None:
        raise admin_error(404, "USER_NOT_FOUND", "회원을 찾을 수 없습니다.")
    return _user_detail(dict(row))


@admin_router.post("/users/{user_id}/subscription")
@admin_router.put("/users/{user_id}/subscription")
async def set_user_subscription(
    user_id: int,
    payload: UpdateSubscriptionRequest,
    _admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        billing = UserBillingRepository(connection)
        # 기존 활성 구독 해제(부분 유니크 인덱스 회피).
        await billing.cancel_subscription(user_id=user_id)
        if payload.plan_type == "free":
            return {"status": "updated", "user_id": user_id, "plan_type": "free"}
        plan_row = await billing.get_plan_by_type(payload.plan_type)
        if plan_row is None:
            raise admin_error(404, "PLAN_NOT_FOUND", "요금제를 찾을 수 없습니다.")
        plan = dict(plan_row)
        await billing.create_subscription(
            user_id=user_id,
            plan_id=int(plan["id"]),
            status=payload.status or "active",
            expires_at=_parse_expires(payload.expires_at),
            payment_method="admin",
        )
    return {"status": "updated", "user_id": user_id, "plan_type": payload.plan_type}


@admin_router.delete("/users/{user_id}/subscription")
async def cancel_user_subscription(
    user_id: int,
    _admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        await UserBillingRepository(connection).cancel_subscription(user_id=user_id)
    return {"status": "cancelled", "user_id": user_id}


@admin_router.get("/stats")
async def get_stats(
    _admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        repository = AdminRepository(connection)
        totals = dict(await repository.subscription_stats())
        by_plan_rows = [dict(row) for row in await repository.subscription_counts_by_plan()]
    return {
        "mrr": int(totals.get("mrr") or 0),
        "total_users": int(totals.get("total_users") or 0),
        "active_subscriptions": int(totals.get("active_subscriptions") or 0),
        "by_plan": {row["plan_type"]: int(row["count"]) for row in by_plan_rows},
        # 실 PG 결제 이력 적재 후 월별 매출 시계열 채움(현재 빈 배열).
        "revenue_monthly": [],
    }


def _user_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row.get("email"),
        "nickname": row.get("nickname"),
        "member_code": row.get("member_code"),
        "created_at": _timestamp(row.get("created_at")),
        "subscription": _subscription_brief(row),
    }


def _user_detail(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row.get("email"),
        "nickname": row.get("nickname"),
        "member_code": row.get("member_code"),
        "agreed_risk": row.get("agreed_risk"),
        "is_verified": row.get("is_verified"),
        "created_at": _timestamp(row.get("created_at")),
        "watchlist_count": int(row.get("watchlist_count") or 0),
        "subscription": _subscription_brief(row),
        "subscription_started_at": _timestamp(row.get("subscription_started_at")),
        "subscription_expires_at": _timestamp(row.get("subscription_expires_at")),
    }


def _subscription_brief(row: dict[str, Any]) -> dict[str, Any] | None:
    plan_type = row.get("plan_type")
    if plan_type is None:
        return None
    return {"plan_type": plan_type, "status": row.get("subscription_status")}


def _parse_expires(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise admin_error(400, "INVALID_EXPIRES_AT", "expires_at 형식이 올바르지 않습니다.") from None


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
