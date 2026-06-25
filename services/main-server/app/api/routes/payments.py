from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.routes.auth import NOTICE, get_current_user
from app.core.config import Settings, get_settings
from app.core.database import get_database_pool
from app.core.email import EmailSender, get_email_sender
from app.core.portone import PortOneClient, PortOneError, get_portone_client, normalize_phone
from signal_alpha_data_access.backend import UserBillingRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments", tags=["payments"])

_SUBSCRIPTION_DAYS = 30


class ConfirmRequest(BaseModel):
    payment_id: str


class CheckoutRequest(BaseModel):
    cycle: str = "monthly"  # monthly | yearly


@router.post("/checkout")
async def checkout(
    payload: CheckoutRequest | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """포트원 V2 결제 파라미터 발급. paymentId 를 생성해 반환한다(프론트가 requestPayment 에 사용).

    이미 활성 구독이면 paymentId 를 발급하지 않는다 → 과금(requestPayment) 자체를 차단해
    '결제 후 중복 거절'로 인한 금전 손실을 막는다.
    """
    user_id = int(current_user["id"])
    payment_id = f"sa-pay-{secrets.token_hex(10)}"
    async with pool.acquire() as connection:
        repository = UserBillingRepository(connection)
        # 활성 구독이 있으면 차단하되, 만료 임박(갱신 허용창) 안이면 연장 결제를 허용한다.
        # → 수동 패스 모델에서 만료 전 갱신으로 이용 공백 방지, 너무 이른 중복결제는 차단.
        active = await repository.get_active_subscription(user_id=user_id)
        if active is not None and active["expires_at"] is not None:
            remaining = (active["expires_at"] - datetime.now(UTC)).total_seconds()
            if remaining > settings.subscription_expiring_soon_days * 86400:
                raise _api_error(
                    409,
                    "ALREADY_SUBSCRIBED",
                    f"이미 구독 중입니다. 만료 {settings.subscription_expiring_soon_days}일 전부터 연장 결제할 수 있습니다.",
                )
        # paymentId↔user 매핑을 미리 적재(status=ready) → webhook 이 먼저 도착해도 사용자 식별 가능.
        await repository.record_portone_verification(
            user_id=user_id,
            imp_uid=payment_id,
            merchant_uid=payment_id,
            status="ready",
            verification_type="payment",
        )
    yearly = payload is not None and payload.cycle == "yearly"
    amount = settings.subscription_price_yearly_krw if yearly else settings.subscription_price_krw
    order_name = "Signal Alpha 연간 구독" if yearly else "Signal Alpha 월 구독"
    return {
        "payment_id": payment_id,
        "amount": amount,
        "order_name": order_name,
        "currency": "CURRENCY_KRW",
        "plan_type": settings.subscription_plan_type,
        # 결제창(이니시스)용 구매자 정보. 전체 전화번호는 결제 컨텍스트에만 노출.
        "customer": {
            "email": current_user.get("email"),
            "full_name": current_user.get("nickname"),
            "phone_number": normalize_phone(current_user.get("phone")) or None,
        },
    }


@router.post("/confirm")
async def confirm(
    payload: ConfirmRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
    portone: PortOneClient = Depends(get_portone_client),
    email_sender: EmailSender = Depends(get_email_sender),
) -> dict[str, Any]:
    user_id = int(current_user["id"])
    try:
        payment = await portone.verify_payment(payload.payment_id)
    except PortOneError as exc:
        raise _api_error(400, "PAYMENT_VERIFICATION_FAILED", str(exc)) from None

    # 서버 검증: 상태=paid, 금액=월/연 상품가 중 하나.
    if payment.status != "paid" or payment.amount not in _valid_amounts(settings):
        raise _api_error(400, "PAYMENT_VERIFICATION_FAILED", "결제 검증에 실패했습니다.")

    async with pool.acquire() as connection:
        subscription = await _grant_or_extend(connection, user_id, payment, payload.payment_id, settings)
    if subscription is None:
        raise _api_error(404, "PLAN_NOT_FOUND", "구독 상품을 찾을 수 없습니다.")
    expires = _timestamp(subscription.get("expires_at"))
    await email_sender.send_safe(
        to=current_user.get("email"),
        subject="[Signal Alpha] 결제가 완료되었습니다",
        body=f"구독 결제가 완료되었습니다. 이용 만료일은 {(expires or '')[:10]} 입니다.",
    )
    return {"subscription": _subscription_response(subscription), "notice": NOTICE}


@router.post("/resume")
async def resume(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    email_sender: EmailSender = Depends(get_email_sender),
) -> dict[str, Any]:
    """해지 예약(갱신 중지) 철회 = 재개. cancelled_at 을 비워 정상 활성으로 되돌린다."""
    user_id = int(current_user["id"])
    async with pool.acquire() as connection:
        updated = await UserBillingRepository(connection).resume_subscription(user_id=user_id)
        if updated is None:
            raise _api_error(404, "NO_CANCEL_SCHEDULED", "해지 예약된 구독이 없습니다.")
        expires_at = dict(updated).get("expires_at")
    await email_sender.send_safe(
        to=current_user.get("email"),
        subject="[Signal Alpha] 구독을 계속 이용합니다",
        body="구독 해지 예약이 철회되었습니다. 계속 이용하실 수 있습니다.",
    )
    return {
        "status": "active",
        "expires_at": _timestamp(expires_at),
        "notice": "구독 해지 예약이 철회되었습니다. 계속 이용하실 수 있습니다.",
    }


@router.get("/{payment_id}/receipt")
async def receipt(
    payment_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """결제 영수증 상세(본인 결제만)."""
    user_id = int(current_user["id"])
    async with pool.acquire() as connection:
        record = await UserBillingRepository(connection).get_payment_verification_by_imp_uid(
            imp_uid=payment_id
        )
    if record is None or int(record["user_id"]) != user_id:
        raise _api_error(404, "PAYMENT_NOT_FOUND", "결제 내역을 찾을 수 없습니다.")
    record = dict(record)
    amount = _paid_amount(record, settings)
    return {
        "payment_id": record.get("imp_uid"),
        "order_name": _order_name(amount, settings),
        "amount": amount,
        "status": record.get("status"),
        "paid_at": _timestamp(record.get("verified_at") or record.get("created_at")),
        "customer": {"email": current_user.get("email"), "name": current_user.get("nickname")},
    }


@router.post("/refund")
async def refund(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
    portone: PortOneClient = Depends(get_portone_client),
    email_sender: EmailSender = Depends(get_email_sender),
) -> dict[str, Any]:
    """환불 요청: 결제 후 N일 이내 전액(청약철회), 이후 잔여일 일할 환불 + 즉시 해지."""
    user_id = int(current_user["id"])
    async with pool.acquire() as connection:
        repository = UserBillingRepository(connection)
        active = await repository.get_active_subscription(user_id=user_id)
        if active is None:
            raise _api_error(404, "SUBSCRIPTION_NOT_FOUND", "활성 구독이 없습니다.")
        paid = await repository.list_payment_verifications(user_id=user_id, limit=1)
        if not paid:
            raise _api_error(409, "NO_REFUNDABLE_PAYMENT", "환불 가능한 결제 내역이 없습니다.")
        latest = dict(paid[0])

        now = datetime.now(UTC)
        # 환불 기준 금액은 실제 결제 주기로 결정한다(연간이면 연 상품가). 월가 고정 시
        # 연간 구독이 전액·일할 모두 9,900 기준으로 잘못 계산되던 문제를 바로잡는다.
        price = _cycle_price(active.get("billing_cycle"), settings)
        paid_at = latest.get("verified_at") or latest.get("created_at")
        within_full = (
            paid_at is not None
            and (now - paid_at).total_seconds() <= settings.refund_full_window_days * 86400
        )
        if within_full:
            refund_amount, kind = price, "full"
        else:
            refund_amount, kind = _prorated_refund(active, price, now), "prorated"
        if refund_amount <= 0:
            raise _api_error(409, "NO_REFUNDABLE_AMOUNT", "환불 가능한 잔여 금액이 없습니다.")

        try:
            await portone.cancel_payment(
                str(latest["imp_uid"]),
                reason="user_refund",
                amount=None if kind == "full" else refund_amount,
            )
        except PortOneError as exc:
            logger.warning("환불 PortOne 취소 실패: imp_uid=%s err=%s", latest["imp_uid"], exc)
            # 실제 PortOne 오류를 응답에 노출해 원인 파악이 가능하게 한다(취소 사유는 민감정보 아님).
            raise _api_error(502, "REFUND_FAILED", f"환불 처리에 실패했습니다: {exc}") from None

        await repository.record_portone_verification(
            user_id=user_id,
            imp_uid=str(latest["imp_uid"]),
            merchant_uid=str(latest.get("merchant_uid") or latest["imp_uid"]),
            status="refunded",
            verification_type="payment",
            verified_at=now,
            raw_response=json.dumps({"refund_amount": refund_amount, "kind": kind}),
        )
        await repository.cancel_subscription(user_id=user_id)

    await email_sender.send_safe(
        to=current_user.get("email"),
        subject="[Signal Alpha] 환불이 접수되었습니다",
        body=f"구독이 해지되고 {refund_amount:,}원이 환불 처리되었습니다. 결제수단에 따라 영업일 기준 수일이 소요될 수 있습니다.",
    )
    return {
        "status": "refunded",
        "amount": refund_amount,
        "kind": kind,
        "notice": "환불이 접수되었습니다. 결제수단에 따라 영업일 기준 수일이 소요될 수 있습니다.",
    }


@router.get("/history")
async def history(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """결제 내역(성공 건) 조회. 금액·상품명은 결제 레코드의 실제 결제액으로 표기."""
    user_id = int(current_user["id"])
    async with pool.acquire() as connection:
        rows = await UserBillingRepository(connection).list_payment_verifications(
            user_id=user_id, limit=50
        )
    return {"items": [_payment_item(dict(row), settings) for row in rows]}


@router.post("/webhook")
async def webhook(
    request: Request,
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
    portone: PortOneClient = Depends(get_portone_client),
) -> dict[str, str]:
    """포트원 V2 결제 웹훅. 서명 검증 후 paymentId 로 사용자 매핑→서버 재검증→멱등 반영.

    클라이언트 confirm 누락/위변조에 대비한 신뢰 경로. 처리 불가 이벤트는 200(ack)으로
    응답해 불필요한 재시도를 막는다(서명 실패만 400).
    """
    body = await request.body()
    if settings.portone_webhook_secret:
        if not _verify_webhook_signature(request.headers, body, settings.portone_webhook_secret):
            raise _api_error(400, "WEBHOOK_SIGNATURE_INVALID", "웹훅 서명 검증에 실패했습니다.")
    try:
        event = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return {"status": "ignored"}
    payment_id = _extract_payment_id(event)
    if not payment_id:
        return {"status": "ignored"}
    async with pool.acquire() as connection:
        repository = UserBillingRepository(connection)
        record = await repository.get_payment_verification_by_imp_uid(imp_uid=payment_id)
        if record is None:
            return {"status": "ignored"}  # 우리 스토어 결제가 아니거나 매핑 불가 → ack
        user_id = int(record["user_id"])
        try:
            payment = await portone.verify_payment(payment_id)
        except PortOneError:
            logger.warning("webhook verify_payment 실패: payment_id=%s", payment_id)
            return {"status": "ignored"}
        if payment.status == "paid" and payment.amount in _valid_amounts(settings):
            await _grant_or_extend(connection, user_id, payment, payment_id, settings)
    return {"status": "ok"}


@router.post("/cancel")
async def cancel(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    email_sender: EmailSender = Depends(get_email_sender),
) -> dict[str, Any]:
    """갱신 중지형 해지: 접근권은 만료일까지 유지하고 자동갱신만 멈춘다(즉시 박탈·환불 없음)."""
    user_id = int(current_user["id"])
    async with pool.acquire() as connection:
        repository = UserBillingRepository(connection)
        active = await repository.get_active_subscription(user_id=user_id)
        if active is None:
            raise _api_error(404, "SUBSCRIPTION_NOT_FOUND", "활성 구독이 없습니다.")
        updated = await repository.schedule_subscription_cancel(user_id=user_id)
        expires_at = (dict(updated).get("expires_at") if updated is not None else active["expires_at"])
    await email_sender.send_safe(
        to=current_user.get("email"),
        subject="[Signal Alpha] 구독 해지가 예약되었습니다",
        body=f"구독 해지가 예약되었습니다. {_timestamp(expires_at) or ''}까지 이용 가능하며 이후 종료됩니다.",
    )
    return {
        "status": "cancel_scheduled",
        "expires_at": _timestamp(expires_at),
        "notice": "구독 해지가 예약되었습니다. 만료일까지 그대로 이용할 수 있으며, 환불은 제공되지 않습니다.",
    }


async def _grant_or_extend(
    connection: Any,
    user_id: int,
    payment: Any,
    payment_id: str,
    settings: Settings,
) -> dict[str, Any] | None:
    """결제 1건을 멱등하게 구독으로 반영한다(confirm·webhook 공용).

    - 동일 paymentId 가 이미 'paid' 면 현재 구독을 그대로 반환(이중 연장 방지).
    - 활성 구독이 남아 있으면 만료일부터 30일 누적(연장), 아니면 지금부터 30일.
    """
    repository = UserBillingRepository(connection)
    existing = await repository.get_payment_verification_by_imp_uid(imp_uid=payment_id)
    if existing is not None and str(existing["status"]) == "paid":
        current = await repository.get_subscription_by_user(user_id=user_id)
        return dict(current) if current is not None else None

    plan_row = await repository.get_plan_by_type(settings.subscription_plan_type)
    if plan_row is None:
        return None
    plan = dict(plan_row)

    now = datetime.now(UTC)
    active = await repository.get_active_subscription(user_id=user_id)
    base = now
    if active is not None and active["expires_at"] is not None and active["expires_at"] > now:
        base = active["expires_at"]

    await repository.record_portone_verification(
        user_id=user_id,
        imp_uid=payment_id,
        merchant_uid=payment_id,
        status="paid",
        verification_type="payment",
        verified_at=now,
        raw_response=json.dumps(payment.raw),
    )
    days, billing_cycle = _cycle_for_amount(int(payment.amount), settings)
    await repository.cancel_subscription(user_id=user_id)
    subscription = dict(
        await repository.create_subscription(
            user_id=user_id,
            plan_id=int(plan["id"]),
            status="active",
            expires_at=base + timedelta(days=days),
            payment_method="portone",
            billing_cycle=billing_cycle,
        )
    )
    return {**subscription, **plan}


def _valid_amounts(settings: Settings) -> tuple[int, ...]:
    return (settings.subscription_price_krw, settings.subscription_price_yearly_krw)


def _cycle_for_amount(amount: int, settings: Settings) -> tuple[int, str]:
    """결제 금액으로 구독 주기 판별: 연간가면 365일/yearly, 그 외 월간 30일/monthly."""
    if amount == settings.subscription_price_yearly_krw:
        return 365, "yearly"
    return _SUBSCRIPTION_DAYS, "monthly"


def _cycle_price(billing_cycle: Any, settings: Settings) -> int:
    """구독 주기에 해당하는 상품가(원). 환불 기준액 계산에 사용."""
    if billing_cycle == "yearly":
        return settings.subscription_price_yearly_krw
    return settings.subscription_price_krw


def _order_name(amount: int, settings: Settings) -> str:
    if amount == settings.subscription_price_yearly_krw:
        return "Signal Alpha 연간 구독"
    return "Signal Alpha 월 구독"


def _paid_amount(row: dict[str, Any], settings: Settings) -> int:
    """결제 검증 레코드의 raw_response(JSONB)에서 실제 결제액을 복원한다.

    저장 시 PortOne V2 결제객체(json)를 그대로 넣으므로 amount.total 을 읽는다.
    복원 불가하면 월 상품가로 폴백(과거 레코드 호환)."""
    raw = row.get("raw_response")
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = None
    if isinstance(raw, dict):
        total = (raw.get("amount") or {}).get("total")
        if isinstance(total, int) and total > 0:
            return total
    return settings.subscription_price_krw


def _prorated_refund(active: Any, price: int, now: datetime) -> int:
    """잔여일 비례 환불액. (만료일-지금)/(만료일-시작일) × 결제액. 음수·초과는 클램프."""
    started = active["started_at"] or now
    expires = active["expires_at"]
    if expires is None:
        return 0
    total_days = max(1, int((expires - started).total_seconds() // 86400))
    remaining_days = max(0, int((expires - now).total_seconds() // 86400))
    remaining_days = min(remaining_days, total_days)
    return int(price * remaining_days / total_days)


def _payment_item(row: dict[str, Any], settings: Settings) -> dict[str, Any]:
    amount = _paid_amount(row, settings)
    return {
        "payment_id": row.get("imp_uid"),
        "status": row.get("status"),
        "amount": amount,
        "order_name": _order_name(amount, settings),
        "paid_at": _timestamp(row.get("verified_at") or row.get("created_at")),
    }


def _extract_payment_id(event: Any) -> str | None:
    """포트원 V2 웹훅 본문에서 paymentId 추출(여러 형태 허용)."""
    if isinstance(event, dict):
        data = event.get("data")
        if isinstance(data, dict):
            pid = data.get("paymentId") or data.get("payment_id")
            if pid:
                return str(pid)
        pid = event.get("paymentId") or event.get("payment_id") or event.get("imp_uid")
        if pid:
            return str(pid)
    return None


def _verify_webhook_signature(headers: Any, body: bytes, secret: str) -> bool:
    """standard-webhooks(HMAC-SHA256) 서명 검증. 헤더: webhook-id/-timestamp/-signature."""
    webhook_id = headers.get("webhook-id")
    timestamp = headers.get("webhook-timestamp")
    signature_header = headers.get("webhook-signature")
    if not (webhook_id and timestamp and signature_header):
        return False
    raw_secret = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(raw_secret)
    except ValueError:
        return False
    signed = f"{webhook_id}.{timestamp}.{body.decode('utf-8')}".encode("utf-8")
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode("ascii")
    for token in signature_header.split():
        candidate = token.split(",", 1)[1] if "," in token else token
        if hmac.compare_digest(candidate, expected):
            return True
    return False


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
