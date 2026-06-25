from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, HTTPException, Request, Response

from app.core.config import Settings, get_settings
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import AdminRepository


ADMIN_SESSION_HOURS = 12


async def get_current_admin(
    request: Request,
    settings: Settings = Depends(get_settings),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """관리자 세션 토큰(HttpOnly 쿠키) → admin_sessions 검증 → 관리자 dict 반환.

    user JWT(get_current_user)와 달리 관리자는 DB 세션 토큰 방식이다.
    토큰은 sa_admin 쿠키로 전달되며, 구버전 호환을 위해 Authorization 헤더도 허용한다.
    """
    session_token = request.cookies.get(settings.admin_cookie_name)
    if not session_token:
        authorization = request.headers.get("authorization")
        if authorization and authorization.startswith("Bearer "):
            session_token = authorization.removeprefix("Bearer ").strip()
    if not session_token:
        raise admin_error(401, "ADMIN_AUTH_REQUIRED", "관리자 인증이 필요합니다.")
    async with pool.acquire() as connection:
        session = await AdminRepository(connection).get_active_session(
            session_token=session_token
        )
    if session is None:
        raise admin_error(401, "ADMIN_SESSION_INVALID", "세션이 유효하지 않거나 만료되었습니다.")
    return dict(session)


def set_admin_cookie(
    response: Response, session_token: str, expires_at: datetime, settings: Settings
) -> None:
    max_age = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        key=settings.admin_cookie_name,
        value=session_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path=settings.admin_cookie_path,
        max_age=max_age,
        domain=settings.cookie_domain,
    )


def clear_admin_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.admin_cookie_name,
        path=settings.admin_cookie_path,
        domain=settings.cookie_domain,
    )


def admin_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
