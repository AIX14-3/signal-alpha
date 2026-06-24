from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException

from app.core.database import get_database_pool
from signal_alpha_data_access.repositories import AdminRepository


ADMIN_SESSION_HOURS = 12


async def get_current_admin(
    authorization: str | None = Header(default=None),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """관리자 세션 토큰(Bearer) → admin_sessions 검증 → 관리자 dict 반환.

    user JWT(get_current_user)와 달리 관리자는 DB 세션 토큰 방식이다.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise admin_error(401, "ADMIN_AUTH_REQUIRED", "관리자 인증이 필요합니다.")
    session_token = authorization.removeprefix("Bearer ").strip()
    async with pool.acquire() as connection:
        session = await AdminRepository(connection).get_active_session(
            session_token=session_token
        )
    if session is None:
        raise admin_error(401, "ADMIN_SESSION_INVALID", "세션이 유효하지 않거나 만료되었습니다.")
    return dict(session)


def admin_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
