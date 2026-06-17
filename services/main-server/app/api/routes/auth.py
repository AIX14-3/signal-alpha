from __future__ import annotations

import re
import secrets
from ipaddress import ip_address
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.database import get_database_pool
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from signal_alpha_data_access.repositories import UserBillingRepository, UserSessionRepository


NOTICE = "Signal Alpha는 매수·매도 추천이 아니라 데이터 방향성과 근거를 제공하는 서비스입니다."
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/users", tags=["users"])


class SignupRequest(BaseModel):
    email: str
    password: str
    agreed_risk: bool
    nickname: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


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


@auth_router.post("/signup")
async def signup(
    payload: SignupRequest,
    request: Request,
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    email = _normalize_email(payload.email)
    _validate_email(email)
    _validate_password(payload.password)
    if not payload.agreed_risk:
        raise _api_error(400, "RISK_AGREEMENT_REQUIRED", "서비스 고지 동의가 필요합니다.")

    async with pool.acquire() as connection:
        user_repository = UserBillingRepository(connection)
        existing = await user_repository.get_user_by_email(email)
        if existing is not None:
            raise _api_error(409, "EMAIL_ALREADY_EXISTS", "이미 가입된 이메일입니다.")
        user = dict(
            await user_repository.create_user(
                member_code=_new_member_code(),
                email=email,
                password_hash=hash_password(payload.password),
                nickname=payload.nickname,
                agreed_risk=True,
                is_verified=False,
            )
        )
        tokens = await _issue_tokens(
            connection=connection,
            user_id=int(user["id"]),
            email=str(user["email"]),
            request=request,
            settings=settings,
        )

    return {
        "user": _user_response(user),
        **tokens,
        "notice": NOTICE,
    }


@auth_router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    email = _normalize_email(payload.email)
    async with pool.acquire() as connection:
        user_row = await UserBillingRepository(connection).get_user_by_email(email)
        user = dict(user_row) if user_row is not None else None
        if user is None or not verify_password(payload.password, user.get("password_hash")):
            raise _api_error(401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 올바르지 않습니다.")
        tokens = await _issue_tokens(
            connection=connection,
            user_id=int(user["id"]),
            email=str(user["email"]),
            request=request,
            settings=settings,
        )
    return {
        "user": _user_response(user),
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
async def get_me(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return _user_response(current_user)


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


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _validate_email(email: str) -> None:
    if not EMAIL_PATTERN.match(email):
        raise _api_error(400, "INVALID_EMAIL", "이메일 형식이 올바르지 않습니다.")


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise _api_error(400, "INVALID_PASSWORD", "비밀번호는 최소 8자 이상이어야 합니다.")


def _new_member_code() -> str:
    return f"U{secrets.token_hex(6).upper()}"


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    host = request.client.host
    try:
        ip_address(host)
    except ValueError:
        return None
    return host


def _user_response(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "email": user["email"],
        "nickname": user.get("nickname"),
        "agreed_risk": user["agreed_risk"],
        "is_verified": user.get("is_verified", False),
    }


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
        },
    )
