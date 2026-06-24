from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.routes.auth import NOTICE, _subscription_active, get_current_user
from app.core.config import Settings, get_settings
from app.core.database import get_database_pool
from app.core.portone import PortOneClient, PortOneError, get_portone_client
from signal_alpha_data_access.repositories import UserBillingRepository

router = APIRouter(prefix="/api/payments", tags=["payments"])

_SUBSCRIPTION_DAYS = 30


class ConfirmRequest(BaseModel):
    imp_uid: str
    merchant_uid: str


@router.post("/checkout")
async def checkout(
    current_user: dict[str, Any] = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """포트원 결제창 파라미터 발급. merchant_uid 를 생성해 반환한다."""
    merchant_uid = f"sa_pay_{secrets.token_hex(10)}"
    return {
        "merchant_uid": merchant_uid,
        "amount": settings.subscription_price_krw,
        "name": "Signal Alpha 월 구독",
        "pg": "html5_inicis",
        "plan_type": settings.subscription_plan_type,
    }


@router.post("/confirm")
async def confirm(
    payload: ConfirmRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
    portone: PortOneClient = Depends(get_portone_client),
) -> dict[str, Any]:
    user_id = int(current_user["id"])
    try:
        payment = await portone.verify_payment(payload.imp_uid)
    except PortOneError as exc:
        raise _api_error(400, "PAYMENT_VERIFICATION_FAILED", str(exc)) from None

    # 서버 검증: 상태=paid, 금액=상품가. real 모드는 merchant_uid 도 대조.
    if payment.status != "paid" or payment.amount != settings.subscription_price_krw:
        raise _api_error(400, "PAYMENT_VERIFICATION_FAILED", "결제 검증에 실패했습니다.")
    if not portone.dev_mode and payment.merchant_uid != payload.merchant_uid:
        raise _api_error(400, "PAYMENT_VERIFICATION_FAILED", "주문 정보가 일치하지 않습니다.")

    async with pool.acquire() as connection:
        repository = UserBillingRepository(connection)
        if await _subscription_active(connection, user_id):
            raise _api_error(409, "ALREADY_SUBSCRIBED", "이미 활성 구독이 있습니다.")

        plan_row = await repository.get_plan_by_type(settings.subscription_plan_type)
        if plan_row is None:
            raise _api_error(404, "PLAN_NOT_FOUND", "구독 상품을 찾을 수 없습니다.")
        plan = dict(plan_row)

        await repository.record_portone_verification(
            user_id=user_id,
            imp_uid=payload.imp_uid,
            merchant_uid=payload.merchant_uid,
            status="paid",
            verification_type="payment",
            verified_at=datetime.now(UTC),
            raw_response=json.dumps(payment.raw),
        )
        # 만료된 잔존 active 정리 후 신규 구독 생성(부분 유니크 인덱스 회피).
        await repository.cancel_subscription(user_id=user_id)
        subscription = dict(
            await repository.create_subscription(
                user_id=user_id,
                plan_id=int(plan["id"]),
                status="active",
                expires_at=datetime.now(UTC) + timedelta(days=_SUBSCRIPTION_DAYS),
                payment_method="portone",
                billing_cycle="monthly",
            )
        )
    return {
        "subscription": _subscription_response({**subscription, **plan}),
        "notice": NOTICE,
    }


@router.post("/cancel")
async def cancel(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    portone: PortOneClient = Depends(get_portone_client),
) -> dict[str, Any]:
    user_id = int(current_user["id"])
    async with pool.acquire() as connection:
        repository = UserBillingRepository(connection)
        active = await repository.get_active_subscription(user_id=user_id)
        if active is None:
            raise _api_error(404, "SUBSCRIPTION_NOT_FOUND", "활성 구독이 없습니다.")
        # real 모드: 최근 결제건을 포트원에서 취소(가능한 경우).
        if not portone.dev_mode:
            latest = await repository.get_latest_payment_verification(user_id=user_id)
            if latest is not None:
                try:
                    await portone.cancel_payment(str(latest["imp_uid"]), reason="user_cancel")
                except PortOneError:
                    pass
        await repository.cancel_subscription(user_id=user_id)
    return {"status": "cancelled", "notice": NOTICE}


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
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
