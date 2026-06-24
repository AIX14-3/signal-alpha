from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.database import get_database_pool
from app.core.portone import PortOneClient, PortOneError, get_portone_client
from app.core.social import PROVIDERS, SocialError, resolve_social_identity
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_refresh_token,
)
from signal_alpha_data_access.repositories import UserBillingRepository, UserSessionRepository


NOTICE = "Signal Alpha는 매수·매도 추천이 아니라 데이터 방향성과 근거를 제공하는 서비스입니다."

# member_code = 영문 대문자 4 + 숫자 4 (혼동 문자 I/O/0/1 제외).
_CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_CODE_DIGITS = "23456789"
_MEMBER_CODE_RETRIES = 6
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/users", tags=["users"])


class SignupRequest(BaseModel):
    identity_verification_id: str
    email: str
    nickname: str
    agreed_risk: bool
    agreed_terms: list[str] = []


class LoginRequest(BaseModel):
    identity_verification_id: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class UpdateMeRequest(BaseModel):
    nickname: str | None = None
    email: str | None = None


class SocialAuthRequest(BaseModel):
    code: str
    redirect_uri: str | None = None
    state: str | None = None


async def get_current_user(
    authorization: str | None = Header(default=None),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise _api_error(401, "AUTH_REQUIRED", "인증이 필요합니다.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token, secret_key=settings.auth_secret_key)
    except ValueError:
        raise _api_error(401, "TOKEN_EXPIRED", "토큰이 유효하지 않거나 만료되었습니다.") from None
    async with pool.acquire() as connection:
        user = await UserBillingRepository(connection).get_user_by_id(int(payload["sub"]))
    if user is None:
        raise _api_error(401, "AUTH_REQUIRED", "인증 사용자를 찾을 수 없습니다.")
    return dict(user)


async def get_current_user_optional(
    authorization: str | None = Header(default=None),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any] | None:
    """인증이 있으면 사용자, 없거나 무효면 None(비회원 블라인드 응답용)."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token, secret_key=settings.auth_secret_key)
    except ValueError:
        return None
    async with pool.acquire() as connection:
        user = await UserBillingRepository(connection).get_user_by_id(int(payload["sub"]))
    return dict(user) if user is not None else None


@auth_router.post("/signup")
async def signup(
    payload: SignupRequest,
    request: Request,
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
    portone: PortOneClient = Depends(get_portone_client),
) -> dict[str, Any]:
    if not payload.agreed_risk:
        raise _api_error(400, "RISK_AGREEMENT_REQUIRED", "투자 위험 고지 동의가 필요합니다.")
    email = _normalize_email(payload.email)
    nickname = _clean_nickname(payload.nickname)
    identity = await _verify_identity(portone, payload.identity_verification_id)

    async with pool.acquire() as connection:
        user_repository = UserBillingRepository(connection)
        if await user_repository.get_user_by_phone(identity.phone) is not None:
            raise _api_error(409, "IDENTITY_ALREADY_REGISTERED", "이미 가입된 본인인증 정보입니다.")
        if await user_repository.get_user_by_email(email) is not None:
            raise _api_error(409, "EMAIL_ALREADY_EXISTS", "이미 사용 중인 이메일입니다.")

        user = await _create_identity_user(
            user_repository,
            phone=identity.phone,
            nickname=nickname,
            email=email,
        )
        user_id = int(user["id"])

        await user_repository.record_portone_verification(
            user_id=user_id,
            imp_uid=identity.id,
            merchant_uid=f"identity:{identity.id}",
            status=identity.status,
            verification_type="identity",
            verified_at=datetime.now(UTC),
            raw_response=json.dumps(identity.raw),
        )
        await _record_terms(user_repository, user_id, payload.agreed_terms, request)

        tokens = await _issue_tokens(
            connection=connection,
            user_id=user_id,
            email=str(user["email"]),
            request=request,
            settings=settings,
        )

    return {
        "user": _user_response(user, subscription_active=False),
        **tokens,
        "notice": NOTICE,
    }


@auth_router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
    portone: PortOneClient = Depends(get_portone_client),
) -> dict[str, Any]:
    identity = await _verify_identity(portone, payload.identity_verification_id)
    async with pool.acquire() as connection:
        user_repository = UserBillingRepository(connection)
        user_row = await user_repository.get_user_by_phone(identity.phone)
        if user_row is None:
            raise _api_error(404, "USER_NOT_FOUND", "가입되지 않은 사용자입니다. 회원가입이 필요합니다.")
        user = dict(user_row)
        subscription_active = await _subscription_active(connection, int(user["id"]))
        tokens = await _issue_tokens(
            connection=connection,
            user_id=int(user["id"]),
            email=str(user["email"]),
            request=request,
            settings=settings,
        )
    return {
        "user": _user_response(user, subscription_active=subscription_active),
        **tokens,
        "notice": NOTICE,
    }


@auth_router.post("/refresh")
async def refresh(
    payload: RefreshRequest,
    request: Request,
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    refresh_hash = hash_refresh_token(payload.refresh_token, secret_key=settings.auth_secret_key)
    async with pool.acquire() as connection:
        session_repository = UserSessionRepository(connection)
        session = await session_repository.get_active_session_by_refresh_hash(refresh_hash)
        if session is None:
            raise _api_error(401, "INVALID_REFRESH_TOKEN", "refresh token이 유효하지 않습니다.")
        await session_repository.revoke_session_by_refresh_hash(refresh_hash)
        tokens = await _issue_tokens(
            connection=connection,
            user_id=int(session["user_id"]),
            email=str(session["user_email"]),
            request=request,
            settings=settings,
        )
    return tokens


@auth_router.post("/logout")
async def logout(
    payload: LogoutRequest,
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    refresh_hash = hash_refresh_token(payload.refresh_token, secret_key=settings.auth_secret_key)
    async with pool.acquire() as connection:
        await UserSessionRepository(connection).revoke_session_by_refresh_hash(refresh_hash)
    return {"status": "ok"}


@users_router.get("/me")
async def get_me(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        subscription_active = await _subscription_active(connection, int(current_user["id"]))
    return _user_response(current_user, subscription_active=subscription_active)


@users_router.patch("/me")
async def update_me(
    payload: UpdateMeRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    nickname = _clean_nickname(payload.nickname) if payload.nickname is not None else None
    email = _normalize_email(payload.email) if payload.email is not None else None
    async with pool.acquire() as connection:
        repository = UserBillingRepository(connection)
        if email is not None:
            existing = await repository.get_user_by_email(email)
            if existing is not None and int(existing["id"]) != int(current_user["id"]):
                raise _api_error(409, "EMAIL_ALREADY_EXISTS", "이미 사용 중인 이메일입니다.")
        updated = await repository.update_user_profile(
            user_id=int(current_user["id"]),
            nickname=nickname,
            email=email,
        )
        subscription_active = await _subscription_active(connection, int(current_user["id"]))
    return _user_response(dict(updated), subscription_active=subscription_active)


@users_router.delete("/me")
async def delete_me(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, str]:
    async with pool.acquire() as connection:
        repository = UserBillingRepository(connection)
        # 연동 소셜 토큰도 함께 제거(이후 소셜 로그인 차단).
        for provider in PROVIDERS:
            await repository.delete_social_account(user_id=int(current_user["id"]), provider=provider)
        await repository.soft_delete_user(user_id=int(current_user["id"]))
    return {"status": "deleted"}


# ----- 소셜 연동 -----


@auth_router.get("/social")
async def list_social(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        rows = await UserBillingRepository(connection).list_social_accounts(
            user_id=int(current_user["id"])
        )
    linked = {str(row["provider"]): row for row in rows}
    return {
        "items": [
            {
                "provider": provider,
                "linked": provider in linked,
                "linked_at": _iso(linked[provider]["created_at"]) if provider in linked else None,
            }
            for provider in PROVIDERS
        ]
    }


@auth_router.post("/social/link/{provider}")
async def link_social(
    provider: str,
    payload: SocialAuthRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    provider = _validate_provider(provider)
    identity = await _resolve_social(settings, provider, payload)
    async with pool.acquire() as connection:
        repository = UserBillingRepository(connection)
        existing = await repository.get_social_account(
            provider=provider, provider_user_id=identity.provider_user_id
        )
        if existing is not None and int(existing["user_id"]) != int(current_user["id"]):
            raise _api_error(409, "SOCIAL_ALREADY_LINKED", "이미 다른 계정에 연동된 소셜 계정입니다.")
        row = await repository.upsert_social_account(
            user_id=int(current_user["id"]),
            provider=provider,
            provider_user_id=identity.provider_user_id,
            access_token=identity.access_token,
            refresh_token=identity.refresh_token,
        )
    return {"provider": provider, "linked": True, "linked_at": _iso(dict(row).get("created_at"))}


@auth_router.post("/social/login/{provider}")
async def social_login(
    provider: str,
    payload: SocialAuthRequest,
    request: Request,
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    provider = _validate_provider(provider)
    identity = await _resolve_social(settings, provider, payload)
    async with pool.acquire() as connection:
        repository = UserBillingRepository(connection)
        account = await repository.get_social_account(
            provider=provider, provider_user_id=identity.provider_user_id
        )
        if account is None:
            raise _api_error(
                404,
                "SOCIAL_NOT_LINKED",
                "연동된 계정이 없습니다. 본인인증으로 가입 후 소셜 연동을 진행해 주세요.",
            )
        account = dict(account)
        user = dict(await repository.get_user_by_id(int(account["user_id"])))
        subscription_active = await _subscription_active(connection, int(user["id"]))
        tokens = await _issue_tokens(
            connection=connection,
            user_id=int(user["id"]),
            email=str(account["user_email"]),
            request=request,
            settings=settings,
        )
    return {
        "user": _user_response(user, subscription_active=subscription_active),
        **tokens,
        "notice": NOTICE,
    }


@auth_router.delete("/social/{provider}")
async def unlink_social(
    provider: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    provider = _validate_provider(provider)
    async with pool.acquire() as connection:
        await UserBillingRepository(connection).delete_social_account(
            user_id=int(current_user["id"]), provider=provider
        )
    return {"provider": provider, "linked": False}


# ----- helpers -----


def _validate_provider(provider: str) -> str:
    value = provider.strip().lower()
    if value not in PROVIDERS:
        raise _api_error(404, "PROVIDER_NOT_FOUND", "지원하지 않는 소셜 제공자입니다.")
    return value


async def _resolve_social(settings: Settings, provider: str, payload: SocialAuthRequest) -> Any:
    try:
        return await resolve_social_identity(
            settings, provider, payload.code, payload.redirect_uri, payload.state
        )
    except SocialError as exc:
        raise _api_error(400, "SOCIAL_AUTH_FAILED", str(exc)) from None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


async def _verify_identity(portone: PortOneClient, identity_verification_id: str) -> Any:
    try:
        identity = await portone.verify_identity(identity_verification_id)
    except PortOneError as exc:
        raise _api_error(400, "IDENTITY_VERIFICATION_FAILED", str(exc)) from None
    if identity.status != "certified" or not identity.phone:
        raise _api_error(400, "IDENTITY_VERIFICATION_FAILED", "본인인증에 실패했습니다.")
    return identity


async def _create_identity_user(
    user_repository: UserBillingRepository,
    *,
    phone: str,
    nickname: str,
    email: str,
) -> dict[str, Any]:
    """member_code 충돌 시 재시도하며 신규 회원을 INSERT 한다."""
    for _ in range(_MEMBER_CODE_RETRIES):
        member_code = _new_member_code()
        try:
            row = await user_repository.insert_identity_user(
                member_code=member_code,
                email=email,
                phone=phone,
                nickname=nickname,
            )
            return dict(row)
        except asyncpg.UniqueViolationError as exc:
            constraint = getattr(exc, "constraint_name", "") or ""
            if "phone" in constraint:
                raise _api_error(
                    409, "IDENTITY_ALREADY_REGISTERED", "이미 가입된 본인인증 정보입니다."
                ) from None
            if "email" in constraint:
                raise _api_error(409, "EMAIL_ALREADY_EXISTS", "이미 사용 중인 이메일입니다.") from None
            # member_code 충돌 → 재생성 재시도
            continue
    raise _api_error(500, "MEMBER_CODE_GENERATION_FAILED", "회원 식별번호 생성에 실패했습니다.")


async def _record_terms(
    user_repository: UserBillingRepository,
    user_id: int,
    agreed_terms: list[str],
    request: Request,
) -> None:
    ip = _client_ip(request)
    for terms_type in {"risk", *agreed_terms}:
        await user_repository.record_terms_agreement(
            user_id=user_id,
            terms_type=terms_type,
            version="1.0",
            agreed=True,
            ip_address=ip,
        )


async def _subscription_active(connection: Any, user_id: int) -> bool:
    subscription = await UserBillingRepository(connection).get_active_subscription(user_id=user_id)
    if subscription is None:
        return False
    expires_at = subscription.get("expires_at")
    return expires_at is None or expires_at > datetime.now(UTC)


async def _issue_tokens(
    *,
    connection: Any,
    user_id: int,
    email: str,
    request: Request,
    settings: Settings,
) -> dict[str, str]:
    access_token = create_access_token(
        user_id=user_id,
        email=email,
        secret_key=settings.auth_secret_key,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )
    refresh_token = create_refresh_token()
    refresh_hash = hash_refresh_token(refresh_token, secret_key=settings.auth_secret_key)
    await UserSessionRepository(connection).create_session(
        user_id=user_id,
        refresh_token_hash=refresh_hash,
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


def _new_member_code() -> str:
    letters = "".join(secrets.choice(_CODE_LETTERS) for _ in range(4))
    digits = "".join(secrets.choice(_CODE_DIGITS) for _ in range(4))
    return f"{letters}{digits}"


def _normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not _EMAIL_PATTERN.match(value):
        raise _api_error(400, "INVALID_EMAIL", "이메일 형식이 올바르지 않습니다.")
    return value


def _clean_nickname(nickname: str | None) -> str:
    value = (nickname or "").strip()
    if not value:
        raise _api_error(400, "INVALID_NICKNAME", "닉네임을 입력해 주세요.")
    return value[:50]


def _mask_phone(phone: str | None) -> str | None:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) < 7:
        return None
    return f"{digits[:3]}-****-{digits[-4:]}"


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    host = request.client.host
    try:
        ip_address(host)
    except ValueError:
        return None
    return host


def _user_response(user: dict[str, Any], *, subscription_active: bool) -> dict[str, Any]:
    return {
        "id": user["id"],
        "member_code": user.get("member_code"),
        "nickname": user.get("nickname"),
        "email": user.get("email"),
        "phone_masked": _mask_phone(user.get("phone")),
        "agreed_risk": user.get("agreed_risk", False),
        "subscription_active": subscription_active,
    }


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
        },
    )
