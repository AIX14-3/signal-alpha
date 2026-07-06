"""어드민 결제/구독 — 구독 설정·해지·환불·통계."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.routes.admin._serializers import _audit_json, _parse_dt
from app.api.routes.admin_auth import admin_error, get_current_admin
from app.core.database import get_database_pool
from app.core.portone import PortOneClient, PortOneError, get_portone_client
from signal_alpha_data_access.backend import AdminRepository, UserBillingRepository

router = APIRouter()


class UpdateSubscriptionRequest(BaseModel):
    plan_type: str
    status: str | None = None
    expires_at: str | None = None
    next_billing_at: str | None = None
    auto_renew: bool | None = None


@router.post("/users/{user_id}/subscription")
@router.put("/users/{user_id}/subscription")
async def set_user_subscription(
    user_id: int,
    payload: UpdateSubscriptionRequest,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        billing = UserBillingRepository(connection)
        admin_repo = AdminRepository(connection)
        before = await billing.get_subscription_by_user(user_id=user_id)
        # 기존 활성 구독 해제(부분 유니크 인덱스 회피).
        await billing.cancel_subscription(user_id=user_id)
        if payload.plan_type == "free":
            await admin_repo.record_audit_log(
                actor_admin_id=int(admin["admin_id"]),
                action="subscription.set",
                target_type="subscription",
                target_id=user_id,
                before=_audit_json(dict(before) if before is not None else None),
                after=_audit_json({"plan_type": "free"}),
            )
            return {"status": "updated", "user_id": user_id, "plan_type": "free"}
        plan_row = await billing.get_plan_by_type(payload.plan_type)
        if plan_row is None:
            raise admin_error(404, "PLAN_NOT_FOUND", "요금제를 찾을 수 없습니다.")
        plan = dict(plan_row)
        created = dict(
            await billing.create_subscription(
                user_id=user_id,
                plan_id=int(plan["id"]),
                status=payload.status or "active",
                expires_at=_parse_dt(payload.expires_at, "expires_at"),
                next_billing_at=_parse_dt(payload.next_billing_at, "next_billing_at"),
                auto_renew=bool(payload.auto_renew),
                payment_method="admin",
            )
        )
        await admin_repo.record_audit_log(
            actor_admin_id=int(admin["admin_id"]),
            action="subscription.set",
            target_type="subscription",
            target_id=user_id,
            before=_audit_json(dict(before) if before is not None else None),
            after=_audit_json(created),
        )
    return {"status": "updated", "user_id": user_id, "plan_type": payload.plan_type}


@router.delete("/users/{user_id}/subscription")
async def cancel_user_subscription(
    user_id: int,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        billing = UserBillingRepository(connection)
        cancelled = await billing.cancel_subscription(user_id=user_id)
        await AdminRepository(connection).record_audit_log(
            actor_admin_id=int(admin["admin_id"]),
            action="subscription.cancel",
            target_type="subscription",
            target_id=user_id,
            before=_audit_json(dict(cancelled) if cancelled is not None else None),
        )
    return {"status": "cancelled", "user_id": user_id}


@router.post("/users/{user_id}/refund")
async def refund_user(
    user_id: int,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
    portone: PortOneClient = Depends(get_portone_client),
) -> dict[str, Any]:
    """관리자 전액 환불 + 즉시 해지(최근 성공 결제 기준).

    payments 이력에 환불 행을 append 하고(원 결제행 보존), 구독을 즉시 해지한다.
    """
    async with pool.acquire() as connection:
        repository = UserBillingRepository(connection)
        admin_repo = AdminRepository(connection)
        latest_row = await repository.get_latest_paid_payment(user_id=user_id)
        if latest_row is None:
            raise admin_error(404, "NO_REFUNDABLE_PAYMENT", "환불 가능한 결제 내역이 없습니다.")
        latest = dict(latest_row)
        amount = int(latest["amount"])
        try:
            await portone.cancel_payment(str(latest["imp_uid"]), reason="admin_refund")
        except PortOneError as exc:
            raise admin_error(502, "REFUND_FAILED", f"환불 처리에 실패했습니다: {exc}") from None
        refund_row = dict(
            await repository.record_refund(
                user_id=user_id,
                imp_uid=str(latest["imp_uid"]),
                merchant_uid=str(latest.get("merchant_uid") or latest["imp_uid"]),
                amount=amount,
                refund_amount=amount,
                cancel_reason="admin_refund",
                subscription_id=latest.get("subscription_id"),
            )
        )
        await repository.cancel_subscription(user_id=user_id)
        await admin_repo.record_audit_log(
            actor_admin_id=int(admin["admin_id"]),
            action="payment.refund",
            target_type="payment",
            target_id=int(refund_row["id"]),
            before=_audit_json(latest),
            after=_audit_json(refund_row),
        )
    return {"status": "refunded", "user_id": user_id, "amount": amount}


@router.get("/stats")
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
