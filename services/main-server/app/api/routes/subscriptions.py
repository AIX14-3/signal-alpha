from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.routes.auth import NOTICE, get_current_user
from app.core.database import get_database_pool
from signal_alpha_data_access.repositories import UserBillingRepository


subscriptions_router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])

PLAN_FIELDS = (
    "plan_type",
    "plan_display_name",
    "max_watchlist",
    "signal_delay_hours",
    "journal_max_entries",
    "has_alt_data",
    "has_detail_report",
    "has_backtesting",
    "price_monthly",
    "price_yearly",
)


class SubscribeRequest(BaseModel):
    plan_type: str
    action: Literal["subscribe", "cancel"] = "subscribe"
    billing_cycle: Literal["monthly", "yearly"] = "monthly"


@subscriptions_router.get("/plans")
async def list_plans(pool: Any = Depends(get_database_pool)) -> dict[str, Any]:
    async with pool.acquire() as connection:
        rows = await UserBillingRepository(connection).list_subscription_plans(active_only=True)
    return {"plans": [_plan_response(dict(row)) for row in rows]}


@subscriptions_router.get("/me")
async def get_my_subscription(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        repository = UserBillingRepository(connection)
        active = await repository.get_subscription_by_user(user_id=int(current_user["id"]))
        if active is None:
            free_plan = await repository.get_plan_by_type("free")
            plan = _plan_response(dict(free_plan)) if free_plan is not None else None
            return {"subscription": None, "plan": plan, "notice": NOTICE}
        active = dict(active)
    return {
        "subscription": _subscription_response(active),
        "plan": _plan_response(active),
        "notice": NOTICE,
    }


@subscriptions_router.post("")
async def change_subscription(
    payload: SubscribeRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    user_id = int(current_user["id"])
    async with pool.acquire() as connection:
        repository = UserBillingRepository(connection)

        # 취소 또는 free 전환 → 활성 구독 해제 후 free 상태 반환.
        if payload.action == "cancel" or payload.plan_type == "free":
            await repository.cancel_subscription(user_id=user_id)
            free_plan = await repository.get_plan_by_type("free")
            plan = _plan_response(dict(free_plan)) if free_plan is not None else None
            return {"subscription": None, "plan": plan, "notice": NOTICE}

        plan_row = await repository.get_plan_by_type(payload.plan_type)
        if plan_row is None or not dict(plan_row).get("is_active", True):
            raise _api_error(404, "PLAN_NOT_FOUND", "요금제를 찾을 수 없습니다.")
        plan = dict(plan_row)

        # 활성 구독은 user당 1개(부분 유니크 인덱스) → 기존 활성 취소 후 신규 생성.
        await repository.cancel_subscription(user_id=user_id)
        expires_at = _expires_at(payload.billing_cycle)
        subscription = dict(
            await repository.create_subscription(
                user_id=user_id,
                plan_id=int(plan["id"]),
                status="active",
                expires_at=expires_at,
                billing_cycle=payload.billing_cycle,
            )
        )
    return {
        "subscription": _subscription_response({**subscription, **plan}),
        "plan": _plan_response(plan),
        "notice": NOTICE,
    }


def _expires_at(billing_cycle: str) -> datetime:
    days = 365 if billing_cycle == "yearly" else 30
    return datetime.now(UTC) + timedelta(days=days)


def _plan_response(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in PLAN_FIELDS}


def _subscription_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_type": row.get("plan_type"),
        "status": row.get("status"),
        "started_at": _timestamp(row.get("started_at")),
        "expires_at": _timestamp(row.get("expires_at")),
        "billing_cycle": row.get("billing_cycle"),
    }


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
