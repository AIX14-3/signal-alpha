"""어드민 회원 관리 — 목록/생성/상세/수정/삭제."""

from __future__ import annotations

from typing import Any

from asyncpg import UniqueViolationError
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.routes.admin._serializers import (
    _MEMBER_CODE_RETRIES,
    _admin_email,
    _audit_json,
    _new_member_code,
    _user_detail,
    _user_row,
)
from app.api.routes.admin_auth import admin_error, get_current_admin
from app.core.database import get_database_pool
from app.core.security import hash_password
from signal_alpha_data_access.backend import AdminRepository, UserBillingRepository

router = APIRouter()


class CreateUserRequest(BaseModel):
    email: str
    nickname: str | None = None
    password: str | None = None
    phone: str | None = None


class UpdateUserRequest(BaseModel):
    nickname: str | None = None
    email: str | None = None


@router.get("/users")
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


@router.post("/users", status_code=201)
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


@router.get("/users/{user_id}")
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


@router.patch("/users/{user_id}")
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


@router.delete("/users/{user_id}")
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
