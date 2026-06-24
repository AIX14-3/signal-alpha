from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.routes.auth import NOTICE, get_current_user
from app.core.database import get_database_pool
from signal_alpha_data_access.repositories import UserBillingRepository


# 구독 생성/취소는 결제 검증을 거치는 /api/payments/* 에서 처리한다(여기서는 조회만).
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
