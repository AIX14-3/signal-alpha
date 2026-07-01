from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime, time, timedelta
from typing import Any

from asyncpg import UniqueViolationError
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel

from app.api.routes.admin_auth import (
    ADMIN_SESSION_HOURS,
    admin_error,
    clear_admin_cookie,
    get_current_admin,
    set_admin_cookie,
)
from app.core.config import Settings, get_settings
from app.core.database import get_database_pool
from app.core.portone import PortOneClient, PortOneError, get_portone_client
from app.core.rate_limit import client_ip_from_scope
from app.core.security import create_refresh_token, hash_password, verify_password
from app.core.throttle import get_admin_login_lockout
from signal_alpha_data_access.backend import (
    AdminRepository,
    CollectionScheduleRepository,
    UserBillingRepository,
    parse_schedule_row,
)

# 수집 스케줄 제어 허용값.
_SCHEDULE_TARGETS = {"price", "dart", "report", "alternative"}
_SCHEDULE_PRICE_MODES = {"flows", "snapshot"}


admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminLoginRequest(BaseModel):
    email: str
    password: str


class CreateUserRequest(BaseModel):
    email: str
    nickname: str | None = None
    password: str | None = None
    phone: str | None = None


class UpdateSubscriptionRequest(BaseModel):
    plan_type: str
    status: str | None = None
    expires_at: str | None = None
    next_billing_at: str | None = None
    auto_renew: bool | None = None


class UpdateUserRequest(BaseModel):
    nickname: str | None = None
    email: str | None = None


class UpdateScheduleRequest(BaseModel):
    """수집 스케줄 config 수정(보낸 필드만 변경)."""

    enabled: bool | None = None
    run_at_local: str | None = None  # "HH:MM"
    timezone: str | None = None
    targets: list[str] | None = None
    dart_limit: int | None = None
    price_modes: list[str] | None = None


# member_code = 영문 대문자 4 + 숫자 4 (혼동 문자 I/O/0/1 제외). auth._new_member_code 와 동일 규칙.
_CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_CODE_DIGITS = "23456789"
_MEMBER_CODE_RETRIES = 6
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _new_member_code() -> str:
    letters = "".join(secrets.choice(_CODE_LETTERS) for _ in range(4))
    digits = "".join(secrets.choice(_CODE_DIGITS) for _ in range(4))
    return f"{letters}{digits}"


def _audit_json(row: dict[str, Any] | None) -> str | None:
    """행 스냅샷을 감사로그 JSONB 로 직렬화(datetime 등은 문자열로)."""
    if row is None:
        return None
    serializable = {
        key: (value.isoformat() if hasattr(value, "isoformat") else value)
        for key, value in row.items()
        # 비밀번호 해시 등 민감/대용량 필드는 스냅샷에서 제외.
        if key not in ("password_hash", "raw_response")
    }
    return json.dumps(serializable, ensure_ascii=False, default=str)


def _admin_email(value: str) -> str:
    email = (value or "").strip().lower()
    if not _EMAIL_PATTERN.match(email):
        raise admin_error(400, "INVALID_EMAIL", "이메일 형식이 올바르지 않습니다.")
    return email


@admin_router.post("/login")
async def admin_login(
    payload: AdminLoginRequest,
    request: Request,
    response: Response,
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    email = payload.email.strip().lower()
    # 브루트포스 방어: (이메일+IP) 연속 실패 누적 시 일정시간 잠금.
    lockout = get_admin_login_lockout()
    lock_key = f"{email}:{client_ip_from_scope(request.scope)}"
    retry_after = lockout.retry_after(lock_key)
    if retry_after:
        raise admin_error(
            429, "TOO_MANY_ATTEMPTS", f"로그인 시도가 많습니다. {retry_after}초 후 다시 시도해 주세요."
        )
    async with pool.acquire() as connection:
        repository = AdminRepository(connection)
        admin_row = await repository.get_admin_by_email(email)
        admin = dict(admin_row) if admin_row is not None else None
        if admin is None or not verify_password(payload.password, admin.get("password_hash")):
            lockout.record_failure(lock_key)
            raise admin_error(401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 올바르지 않습니다.")
        lockout.reset(lock_key)
        session_token = create_refresh_token()
        expires_at = datetime.now(UTC) + timedelta(hours=ADMIN_SESSION_HOURS)
        await repository.create_session(
            admin_id=int(admin["id"]),
            session_token=session_token,
            expires_at=expires_at,
        )
        await repository.update_last_login(admin_id=int(admin["id"]))
    # 세션 토큰은 응답 body 가 아니라 HttpOnly 쿠키(sa_admin)로만 전달.
    set_admin_cookie(response, session_token, expires_at, settings)
    return {
        "expires_at": expires_at.isoformat(),
        "admin": {"id": admin["id"], "email": admin["email"]},
    }


@admin_router.get("/me")
async def admin_me(
    admin: dict[str, Any] = Depends(get_current_admin),
) -> dict[str, Any]:
    """쿠키 세션 유효성 확인용(프론트 부팅 시 로그인 상태 판별)."""
    return {"admin": {"id": admin["admin_id"], "email": admin.get("admin_email")}}


@admin_router.post("/logout")
async def admin_logout(
    response: Response,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    async with pool.acquire() as connection:
        await AdminRepository(connection).delete_session(
            session_token=str(admin["session_token"])
        )
    clear_admin_cookie(response, settings)
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


@admin_router.post("/users", status_code=201)
async def create_user(
    payload: CreateUserRequest,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """관리자 회원 생성. 비밀번호 미지정 시 password_hash=NULL(소셜/본인인증 가입과 동일).

    member_code 는 자동 생성(충돌 시 재시도). 이메일 중복 시 409.
    """
    email = _admin_email(payload.email)
    nickname = payload.nickname.strip() if payload.nickname else None
    phone = payload.phone.strip() if payload.phone else None
    password_hash = hash_password(payload.password) if payload.password else None
    async with pool.acquire() as connection:
        billing = UserBillingRepository(connection)
        created: dict[str, Any] | None = None
        for _ in range(_MEMBER_CODE_RETRIES):
            try:
                row = await billing.insert_member(
                    member_code=_new_member_code(),
                    email=email,
                    password_hash=password_hash,
                    nickname=nickname,
                    phone=phone,
                )
                created = dict(row)
                break
            except UniqueViolationError as exc:
                constraint = getattr(exc, "constraint_name", "") or ""
                if "email" in constraint:
                    raise admin_error(409, "EMAIL_EXISTS", "이미 사용 중인 이메일입니다.") from None
                if "phone" in constraint:
                    raise admin_error(409, "PHONE_EXISTS", "이미 사용 중인 휴대폰번호입니다.") from None
                continue  # member_code 충돌 → 재시도
        if created is None:
            raise admin_error(500, "MEMBER_CODE_GENERATION_FAILED", "회원 식별번호 생성에 실패했습니다.")
        await AdminRepository(connection).record_audit_log(
            actor_admin_id=int(admin["admin_id"]),
            action="user.create",
            target_type="user",
            target_id=int(created["id"]),
            after=_audit_json(created),
        )
    return _user_row(created)


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


@admin_router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    payload: UpdateUserRequest,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """회원 닉네임/이메일 수정(보낸 필드만 변경). 이메일 중복 시 409."""
    nickname = payload.nickname.strip() if payload.nickname is not None else None
    email = _admin_email(payload.email) if payload.email is not None else None
    if nickname is None and email is None:
        raise admin_error(400, "NOTHING_TO_UPDATE", "수정할 내용이 없습니다.")
    async with pool.acquire() as connection:
        billing = UserBillingRepository(connection)
        admin_repo = AdminRepository(connection)
        before = await admin_repo.get_user_details(user_id=user_id)
        try:
            updated = await billing.update_user_profile(
                user_id=user_id, nickname=nickname, email=email
            )
        except UniqueViolationError:
            raise admin_error(409, "EMAIL_EXISTS", "이미 사용 중인 이메일입니다.") from None
        if updated is None:
            raise admin_error(404, "USER_NOT_FOUND", "회원을 찾을 수 없습니다.")
        updated = dict(updated)
        await admin_repo.record_audit_log(
            actor_admin_id=int(admin["admin_id"]),
            action="user.update",
            target_type="user",
            target_id=user_id,
            before=_audit_json(dict(before) if before is not None else None),
            after=_audit_json(updated),
        )
    return _user_row(updated)


@admin_router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """회원 영구 삭제(hard delete). DELETE FROM users → FK CASCADE 로 소유 자식 정리,
    analysis_requests 는 SET NULL 로 분리(공용 시그널 보존). 감사로그에 before 스냅샷을 남긴다.
    동일 휴대폰/이메일 재가입은 행이 사라지므로 자연히 허용된다."""
    async with pool.acquire() as connection:
        admin_repo = AdminRepository(connection)
        before = await admin_repo.get_user_details(user_id=user_id)
        if before is None:
            raise admin_error(404, "USER_NOT_FOUND", "회원을 찾을 수 없습니다.")
        await UserBillingRepository(connection).hard_delete_user(user_id=user_id)
        await admin_repo.record_audit_log(
            actor_admin_id=int(admin["admin_id"]),
            action="user.delete",
            target_type="user",
            target_id=user_id,
            before=_audit_json(dict(before) if before is not None else None),
        )
    return {"status": "deleted", "user_id": user_id}


@admin_router.post("/users/{user_id}/subscription")
@admin_router.put("/users/{user_id}/subscription")
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


@admin_router.delete("/users/{user_id}/subscription")
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


@admin_router.post("/users/{user_id}/refund")
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


# ── 수집 스케줄 제어 (collection_schedules) ──────────────────────────────────
# 어드민이 백엔드 DB 의 단일 config 행을 읽고/쓰면, 워커측 스케줄러가 폴링해 발화한다
# (main-server → worker 직접 호출 없음). 상태(last/next run)는 스케줄러가 기록한다.


@admin_router.get("/schedules")
async def list_schedules(
    _admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        rows = await CollectionScheduleRepository(connection).list_all()
    return {"items": [_schedule_row(parse_schedule_row(row)) for row in rows]}


@admin_router.put("/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: int,
    payload: UpdateScheduleRequest,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    run_at = _parse_schedule_time(payload.run_at_local) if payload.run_at_local is not None else None
    targets = _validate_targets(payload.targets) if payload.targets is not None else None
    price_modes = (
        _validate_price_modes(payload.price_modes) if payload.price_modes is not None else None
    )
    if payload.dart_limit is not None and not (1 <= payload.dart_limit <= 1000):
        raise admin_error(400, "INVALID_DART_LIMIT", "dart_limit 은 1~1000 이어야 합니다.")
    async with pool.acquire() as connection:
        repo = CollectionScheduleRepository(connection)
        before = await repo.get_by_id(schedule_id)
        if before is None:
            raise admin_error(404, "SCHEDULE_NOT_FOUND", "스케줄을 찾을 수 없습니다.")
        updated = await repo.update_config(
            schedule_id=schedule_id,
            enabled=payload.enabled,
            run_at_local=run_at,
            timezone=payload.timezone,
            targets=targets,
            dart_limit=payload.dart_limit,
            price_modes=price_modes,
            updated_by=str(admin.get("admin_email") or admin.get("admin_id")),
        )
        await AdminRepository(connection).record_audit_log(
            actor_admin_id=int(admin["admin_id"]),
            action="schedule.update",
            target_type="schedule",
            target_id=schedule_id,
            before=_audit_json(parse_schedule_row(before)),
            after=_audit_json(parse_schedule_row(updated)),
        )
    return _schedule_row(parse_schedule_row(updated))


@admin_router.post("/schedules/{schedule_id}/trigger")
async def trigger_schedule(
    schedule_id: int,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """'지금 실행' — manual_trigger 표시. 워커 스케줄러가 다음 폴링에 발화한다."""
    async with pool.acquire() as connection:
        repo = CollectionScheduleRepository(connection)
        updated = await repo.request_manual_trigger(
            schedule_id=schedule_id,
            updated_by=str(admin.get("admin_email") or admin.get("admin_id")),
        )
        if updated is None:
            raise admin_error(404, "SCHEDULE_NOT_FOUND", "스케줄을 찾을 수 없습니다.")
        await AdminRepository(connection).record_audit_log(
            actor_admin_id=int(admin["admin_id"]),
            action="schedule.trigger",
            target_type="schedule",
            target_id=schedule_id,
        )
    return _schedule_row(parse_schedule_row(updated))


def _parse_schedule_time(value: str) -> time:
    try:
        hour, minute = value.strip().split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        raise admin_error(400, "INVALID_TIME", "시각 형식(HH:MM)이 올바르지 않습니다.") from None


def _validate_targets(targets: list[str]) -> list[str]:
    cleaned = [t.strip().lower() for t in targets if t and t.strip()]
    invalid = [t for t in cleaned if t not in _SCHEDULE_TARGETS]
    if invalid:
        raise admin_error(400, "INVALID_TARGETS", f"허용되지 않은 대상: {invalid}")
    return cleaned


def _validate_price_modes(modes: list[str]) -> list[str]:
    cleaned = [m.strip().lower() for m in modes if m and m.strip()]
    invalid = [m for m in cleaned if m not in _SCHEDULE_PRICE_MODES]
    if invalid:
        raise admin_error(400, "INVALID_PRICE_MODES", f"허용되지 않은 모드: {invalid}")
    return cleaned


def _schedule_row(row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    run_at = row.get("run_at_local")
    detail = row.get("last_detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except (ValueError, TypeError):
            pass
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "enabled": row.get("enabled"),
        "run_at_local": run_at.strftime("%H:%M") if hasattr(run_at, "strftime") else run_at,
        "timezone": row.get("timezone"),
        "targets": row.get("targets") or [],
        "dart_limit": row.get("dart_limit"),
        "price_modes": row.get("price_modes") or [],
        "last_run_at": _timestamp(row.get("last_run_at")),
        "last_status": row.get("last_status"),
        "last_detail": detail,
        "next_run_at": _timestamp(row.get("next_run_at")),
        "manual_trigger_requested_at": _timestamp(row.get("manual_trigger_requested_at")),
        "updated_by": row.get("updated_by"),
        "updated_at": _timestamp(row.get("updated_at")),
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


def _parse_dt(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise admin_error(
            400, "INVALID_DATETIME", f"{field} 형식이 올바르지 않습니다."
        ) from None


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
