"""관리자 커뮤니티 모더레이션 라우트."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.routes.admin._serializers import _audit_json, _timestamp
from app.api.routes.admin_auth import admin_error, get_current_admin
from app.api.routes.community import _post_response
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import AdminRepository, CommunityRepository

router = APIRouter(prefix="/community")

_TARGET_TYPES = {"all", "post", "comment"}
_TARGET_RANKS = {"comment": 0, "post": 1}
_MAX_LIMIT = 100


def _report_reasons(row: dict[str, Any]) -> list[str]:
    reasons = row.get("report_reasons") or []
    return [str(reason) for reason in reasons if reason]


def _sort_at(row: dict[str, Any]) -> datetime | None:
    value = row.get("latest_reported_at") or row.get("updated_at") or row.get("created_at")
    return value if isinstance(value, datetime) else None


def _sort_value(row: dict[str, Any]) -> float:
    value = _sort_at(row)
    return float(value.timestamp()) if value is not None else 0.0


def _sort_key(kind: str, row: dict[str, Any]) -> tuple[float, int, int]:
    return (_sort_value(row), _TARGET_RANKS[kind], int(row["id"]))


def _format_cursor(kind: str, row: dict[str, Any]) -> str | None:
    sort_at = _sort_at(row)
    if sort_at is None:
        return None
    sort_us = int(sort_at.timestamp() * 1_000_000)
    return f"{sort_us}|{kind}|{int(row['id'])}"


def _parse_cursor(cursor: str | None) -> tuple[datetime | None, int | None, int | None]:
    if cursor is None:
        return None, None, None
    try:
        sort_text, kind, id_text = cursor.split("|", 2)
        return datetime.fromtimestamp(int(sort_text) / 1_000_000, UTC), _TARGET_RANKS[kind], int(id_text)
    except (KeyError, ValueError):
        raise admin_error(400, "INVALID_CURSOR", "cursor 형식이 올바르지 않습니다.") from None


def _moderation_base(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": row.get("status"),
        "report_count": int(row.get("report_count") or 0),
        "report_reasons": _report_reasons(row),
        "latest_reported_at": _timestamp(row.get("latest_reported_at")),
    }


def _moderation_post(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **_moderation_base(row),
        "target_type": "post",
        "id": row["id"],
        "post": _post_response(row),
    }


def _moderation_comment(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **_moderation_base(row),
        "target_type": "comment",
        "id": row["id"],
        "post_id": row["post_id"],
        "post_title": row.get("post_title"),
        "parent_comment_id": row.get("parent_comment_id"),
        "body": row.get("body"),
        "author": {
            "member_code": row.get("author_member_code"),
            "nickname": row.get("author_nickname"),
        },
        "created_at": _timestamp(row.get("created_at")),
        "updated_at": _timestamp(row.get("updated_at")),
    }


@router.get("/moderation")
async def list_community_moderation(
    target_type: str = Query(default="all"),
    limit: int = Query(default=20, ge=1, le=_MAX_LIMIT),
    cursor: str | None = Query(default=None),
    _admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    if target_type not in _TARGET_TYPES:
        raise admin_error(400, "INVALID_TARGET_TYPE", "target_type은 all, post, comment 중 하나여야 합니다.")
    cursor_sort_at, cursor_target_rank, cursor_id = _parse_cursor(cursor)
    fetch_limit = limit + 1
    async with pool.acquire() as connection:
        repository = CommunityRepository(connection)
        rows: list[tuple[str, dict[str, Any]]] = []
        if target_type in {"all", "post"}:
            rows.extend(
                ("post", dict(row))
                for row in await repository.list_hidden_posts(
                    limit=fetch_limit,
                    cursor_sort_at=cursor_sort_at,
                    cursor_target_rank=cursor_target_rank,
                    cursor_id=cursor_id,
                )
            )
        if target_type in {"all", "comment"}:
            rows.extend(
                ("comment", dict(row))
                for row in await repository.list_hidden_comments(
                    limit=fetch_limit,
                    cursor_sort_at=cursor_sort_at,
                    cursor_target_rank=cursor_target_rank,
                    cursor_id=cursor_id,
                )
            )
    rows.sort(key=lambda item: _sort_key(item[0], item[1]), reverse=True)
    page_rows = rows[:limit]
    items = [
        _moderation_post(row) if kind == "post" else _moderation_comment(row)
        for kind, row in page_rows
    ]
    next_cursor = _format_cursor(*page_rows[-1]) if len(rows) > limit else None
    return {"items": items, "target_type": target_type, "next_cursor": next_cursor}


async def _audit(
    connection: Any,
    *,
    admin: dict[str, Any],
    action: str,
    target_type: str,
    target_id: int,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None = None,
) -> None:
    await AdminRepository(connection).record_audit_log(
        actor_admin_id=int(admin["admin_id"]),
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=_audit_json(before),
        after=_audit_json(after),
    )


@router.post("/posts/{post_id}/restore")
async def restore_community_post(
    post_id: int,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        repository = CommunityRepository(connection)
        before_row = await repository.get_hidden_post_for_moderation(post_id=post_id)
        if before_row is None:
            raise admin_error(404, "MODERATION_TARGET_NOT_FOUND", "검토 대상 게시글을 찾을 수 없습니다.")
        restored = await repository.restore_hidden_post(post_id=post_id)
        if not restored:
            raise admin_error(404, "MODERATION_TARGET_NOT_FOUND", "검토 대상 게시글을 찾을 수 없습니다.")
        await _audit(
            connection,
            admin=admin,
            action="community.post.restore",
            target_type="community_post",
            target_id=post_id,
            before=dict(before_row),
            after={"status": "visible"},
        )
    return {"status": "visible", "target_type": "post", "id": post_id}


@router.post("/comments/{comment_id}/restore")
async def restore_community_comment(
    comment_id: int,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        repository = CommunityRepository(connection)
        before_row = await repository.get_hidden_comment_for_moderation(comment_id=comment_id)
        if before_row is None:
            raise admin_error(404, "MODERATION_TARGET_NOT_FOUND", "검토 대상 댓글을 찾을 수 없습니다.")
        restored = await repository.restore_hidden_comment(comment_id=comment_id)
        if not restored:
            raise admin_error(404, "MODERATION_TARGET_NOT_FOUND", "검토 대상 댓글을 찾을 수 없습니다.")
        await _audit(
            connection,
            admin=admin,
            action="community.comment.restore",
            target_type="community_comment",
            target_id=comment_id,
            before=dict(before_row),
            after={"status": "visible"},
        )
    return {"status": "visible", "target_type": "comment", "id": comment_id}


@router.delete("/posts/{post_id}")
async def delete_community_post(
    post_id: int,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        repository = CommunityRepository(connection)
        before_row = await repository.get_hidden_post_for_moderation(post_id=post_id)
        if before_row is None:
            raise admin_error(404, "MODERATION_TARGET_NOT_FOUND", "검토 대상 게시글을 찾을 수 없습니다.")
        deleted = await repository.delete_hidden_post(post_id=post_id)
        if not deleted:
            raise admin_error(404, "MODERATION_TARGET_NOT_FOUND", "검토 대상 게시글을 찾을 수 없습니다.")
        await _audit(
            connection,
            admin=admin,
            action="community.post.delete",
            target_type="community_post",
            target_id=post_id,
            before=dict(before_row),
        )
    return {"status": "deleted", "target_type": "post", "id": post_id}


@router.delete("/comments/{comment_id}")
async def delete_community_comment(
    comment_id: int,
    admin: dict[str, Any] = Depends(get_current_admin),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        repository = CommunityRepository(connection)
        before_row = await repository.get_hidden_comment_for_moderation(comment_id=comment_id)
        if before_row is None:
            raise admin_error(404, "MODERATION_TARGET_NOT_FOUND", "검토 대상 댓글을 찾을 수 없습니다.")
        deleted = await repository.delete_hidden_comment(comment_id=comment_id)
        if not deleted:
            raise admin_error(404, "MODERATION_TARGET_NOT_FOUND", "검토 대상 댓글을 찾을 수 없습니다.")
        await _audit(
            connection,
            admin=admin,
            action="community.comment.delete",
            target_type="community_comment",
            target_id=comment_id,
            before=dict(before_row),
        )
    return {"status": "deleted", "target_type": "comment", "id": comment_id}
