"""어드민 인증 — 로그인/세션 확인/로그아웃(sa_admin HttpOnly 쿠키)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
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
from app.core.rate_limit import client_ip_from_scope
from app.core.security import create_refresh_token, verify_password
from app.core.throttle import get_admin_login_lockout
from signal_alpha_data_access.backend import AdminRepository

router = APIRouter()


class AdminLoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
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


@router.get("/me")
async def admin_me(
    admin: dict[str, Any] = Depends(get_current_admin),
) -> dict[str, Any]:
    """쿠키 세션 유효성 확인용(프론트 부팅 시 로그인 상태 판별)."""
    return {"admin": {"id": admin["admin_id"], "email": admin.get("admin_email")}}


@router.post("/logout")
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
