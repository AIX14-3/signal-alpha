"""저널 커뮤니티 게시판 라우트.

유저가 저널(투자 판단 기록)을 공개 공유하는 소셜 레이어. 읽기 전체 공개(비로그인 포함),
쓰기(게시/댓글/반응/신고) 로그인. 공개 응답은 화이트리스트만 — 저널의 가격/절대손익·PII 는
노출하지 않는다(수익률%는 show_pnl opt-in 시에만). 저자·저널은 라이브 참조.

spec: docs/spec/journal-board-spec.md
"""

from __future__ import annotations

import hashlib
import secrets
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.api.routes.auth import (
    NOTICE,
    get_current_user,
    get_current_user_optional,
)
from app.core.config import Settings, get_settings
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import CommunityRepository

router = APIRouter(prefix="/api/community", tags=["community"])

# 서로 다른 신고자 수가 이 값 이상이면 대상 자동 숨김(status='hidden'). 운영 튜닝값.
REPORT_HIDE_THRESHOLD = 5
_FEED_MAX = 50
_REACTION_TYPES = {"like", "bookmark"}
_VIEWER_COOKIE = "sa_community_viewer"
_VIEWER_COOKIE_MAX_AGE_SECONDS = 365 * 86400


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


# ---- 요청 모델 -------------------------------------------------------------
class PostCreateRequest(BaseModel):
    title: str
    body: str = ""
    journal_id: int | None = None
    show_pnl: bool = False


class PostUpdateRequest(BaseModel):
    title: str | None = None
    body: str | None = None
    show_pnl: bool | None = None


class CommentCreateRequest(BaseModel):
    body: str
    parent_comment_id: int | None = None


class ReactionRequest(BaseModel):
    type: str = Field(description="like | bookmark")


class ReportRequest(BaseModel):
    reason: str | None = None


# ---- 응답 헬퍼 -------------------------------------------------------------
def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _timestamp(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _post_response(row: dict[str, Any]) -> dict[str, Any]:
    journal = None
    # journal_id 가 NULL 이면 원본 저널이 삭제됐거나(SET NULL) 애초에 없는 것 → "원본 없음".
    if row.get("journal_id") is not None:
        stock = None
        if row.get("stock_ticker"):
            stock = {"ticker": row.get("stock_ticker"), "name": row.get("stock_name")}
        journal = {
            "user_view": row.get("journal_user_view"),
            "user_memo": row.get("journal_user_memo"),
            "stock": stock,
            # show_pnl 이 켜진 게시글만 수익률%; 아니면 리포지토리가 NULL 로 내려준다.
            "pnl_pct": _number(row.get("pnl_pct")),
        }
    response = {
        "id": row["id"],
        "title": row["title"],
        "body": row["body"],
        "author": {
            "member_code": row.get("author_member_code"),
            "nickname": row.get("author_nickname"),
        },
        "journal": journal,
        "show_pnl": row.get("show_pnl", False),
        "view_count": row.get("view_count", 0),
        "like_count": row.get("like_count", 0),
        "bookmark_count": row.get("bookmark_count", 0),
        "comment_count": row.get("comment_count", 0),
        "my_reactions": {
            "like": bool(row.get("my_like", False)),
            "bookmark": bool(row.get("my_bookmark", False)),
        },
        "created_at": _timestamp(row.get("created_at")),
        "updated_at": _timestamp(row.get("updated_at")),
    }
    if row.get("ranking_score") is not None:
        response["ranking_score"] = _number(row.get("ranking_score"))
    return response


def _comment_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "post_id": row["post_id"],
        "parent_comment_id": row.get("parent_comment_id"),
        "body": row["body"],
        "author": {
            "member_code": row.get("author_member_code"),
            "nickname": row.get("author_nickname"),
        },
        "like_count": row.get("like_count", 0),
        "bookmark_count": row.get("bookmark_count", 0),
        "my_reactions": {
            "like": bool(row.get("my_like", False)),
            "bookmark": bool(row.get("my_bookmark", False)),
        },
        "created_at": _timestamp(row.get("created_at")),
    }


async def _attach_my_reactions(
    repo: CommunityRepository,
    rows: list[dict[str, Any]],
    current_user: dict[str, Any] | None,
    target_type: str,
) -> None:
    for row in rows:
        row["my_like"] = False
        row["my_bookmark"] = False
    if current_user is None or not rows:
        return
    reactions = await repo.list_user_reactions(
        user_id=int(current_user["id"]),
        target_type=target_type,
        target_ids=[int(row["id"]) for row in rows],
    )
    by_target: dict[int, set[str]] = {}
    for reaction in reactions:
        reaction_row = dict(reaction)
        by_target.setdefault(int(reaction_row["target_id"]), set()).add(str(reaction_row["type"]))
    for row in rows:
        types = by_target.get(int(row["id"]), set())
        row["my_like"] = "like" in types
        row["my_bookmark"] = "bookmark" in types


def _anonymous_viewer_key(request: Request, response: Response, settings: Settings) -> str:
    token = request.cookies.get(_VIEWER_COOKIE)
    if not token:
        token = secrets.token_urlsafe(24)
        response.set_cookie(
            key=_VIEWER_COOKIE,
            value=token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
            path="/",
            max_age=_VIEWER_COOKIE_MAX_AGE_SECONDS,
            domain=settings.cookie_domain,
        )
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _viewer_key(
    current_user: dict[str, Any] | None,
    request: Request,
    response: Response,
    settings: Settings,
) -> str:
    if current_user is not None:
        return f"u{int(current_user['id'])}"
    return _anonymous_viewer_key(request, response, settings)


def _parse_popular_cursor(cursor: str | None) -> tuple[Decimal | None, int | None]:
    if cursor is None:
        return None, None
    try:
        score_text, id_text = cursor.split(":", 1)
        return Decimal(score_text), int(id_text)
    except (InvalidOperation, ValueError):
        raise _api_error(400, "INVALID_CURSOR", "cursor 형식이 올바르지 않습니다.") from None


def _format_popular_cursor(row: dict[str, Any]) -> str | None:
    score = row.get("ranking_score")
    if score is None:
        return None
    if isinstance(score, Decimal):
        score_text = format(score, "f")
    else:
        score_text = format(Decimal(str(score)), "f")
    return f"{score_text}:{row['id']}"


# ---- 피드 / 인기 (공개, 비로그인 열람 가능) --------------------------------
@router.get("/posts")
async def list_feed(
    cursor: int | None = None,
    limit: int = 20,
    current_user: dict[str, Any] | None = Depends(get_current_user_optional),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    clamped = min(max(limit, 1), _FEED_MAX)
    async with pool.acquire() as connection:
        repo = CommunityRepository(connection)
        rows = [dict(row) for row in await repo.list_feed(cursor_id=cursor, limit=clamped)]
        await _attach_my_reactions(repo, rows, current_user, "post")
    items = [_post_response(row) for row in rows]
    next_cursor = items[-1]["id"] if len(items) == clamped else None
    return {"items": items, "next_cursor": next_cursor, "notice": NOTICE}


@router.get("/popular")
async def list_popular(
    window: str = "weekly",
    limit: int = 20,
    cursor: str | None = None,
    current_user: dict[str, Any] | None = Depends(get_current_user_optional),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    if window not in {"weekly", "all"}:
        raise _api_error(400, "INVALID_WINDOW", "window 는 weekly 또는 all 이어야 합니다.")
    clamped = min(max(limit, 1), _FEED_MAX)
    cursor_score, cursor_id = _parse_popular_cursor(cursor)
    async with pool.acquire() as connection:
        repo = CommunityRepository(connection)
        rows = await repo.list_popular(
            window_kind=window,
            limit=clamped + 1,
            cursor_score=cursor_score,
            cursor_id=cursor_id,
        )
    row_dicts = [dict(row) for row in rows]
    page_rows = row_dicts[:clamped]
    async with pool.acquire() as connection:
        await _attach_my_reactions(CommunityRepository(connection), page_rows, current_user, "post")
    next_cursor = _format_popular_cursor(page_rows[-1]) if len(row_dicts) > clamped else None
    items = [_post_response(row) for row in page_rows]
    return {"items": items, "next_cursor": next_cursor, "notice": NOTICE}


# ---- 게시글 작성/조회/수정/삭제 --------------------------------------------
@router.post("/posts")
async def create_post(
    payload: PostCreateRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    title = payload.title.strip()
    if not title:
        raise _api_error(400, "TITLE_REQUIRED", "제목을 입력해 주세요.")
    user_id = int(current_user["id"])
    async with pool.acquire() as connection:
        repo = CommunityRepository(connection)
        if payload.journal_id is not None:
            owner = await repo.get_journal_author(journal_id=payload.journal_id)
            if owner is None:
                raise _api_error(404, "JOURNAL_NOT_FOUND", "저널을 찾을 수 없습니다.")
            if int(owner) != user_id:
                raise _api_error(403, "NOT_JOURNAL_OWNER", "본인 저널만 공유할 수 있습니다.")
        row = await repo.create_post(
            author_user_id=user_id,
            journal_id=payload.journal_id,
            title=title,
            body=payload.body,
            show_pnl=payload.show_pnl,
        )
    return _post_response(dict(row))


@router.get("/posts/{post_id}")
async def get_post(
    post_id: int,
    request: Request,
    response: Response,
    current_user: dict[str, Any] | None = Depends(get_current_user_optional),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        repo = CommunityRepository(connection)
        row = await repo.get_post(post_id=post_id)
        if row is None:
            raise _api_error(404, "POST_NOT_FOUND", "게시글을 찾을 수 없습니다.")
        # 조회수 중복방지: 로그인은 유저, 비로그인은 세션 쿠키 해시 기준으로 1일 1회 집계.
        row_dict = dict(row)
        await _attach_my_reactions(repo, [row_dict], current_user, "post")
        await repo.record_view(
            post_id=post_id,
            viewer_key=_viewer_key(current_user, request, response, settings),
        )
    return _post_response(row_dict)


@router.patch("/posts/{post_id}")
async def update_post(
    post_id: int,
    payload: PostUpdateRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        row = await CommunityRepository(connection).update_post(
            post_id=post_id,
            author_user_id=int(current_user["id"]),
            title=payload.title,
            body=payload.body,
            show_pnl=payload.show_pnl,
        )
    if row is None:
        raise _api_error(404, "POST_NOT_FOUND", "게시글이 없거나 수정 권한이 없습니다.")
    return _post_response(dict(row))


@router.delete("/posts/{post_id}")
async def delete_post(
    post_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        deleted = await CommunityRepository(connection).soft_delete_post(
            post_id=post_id, author_user_id=int(current_user["id"])
        )
    if not deleted:
        raise _api_error(404, "POST_NOT_FOUND", "게시글이 없거나 삭제 권한이 없습니다.")
    return {"deleted": True}


# ---- 댓글 -----------------------------------------------------------------
@router.get("/posts/{post_id}/comments")
async def list_comments(
    post_id: int,
    current_user: dict[str, Any] | None = Depends(get_current_user_optional),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        repo = CommunityRepository(connection)
        rows = [dict(row) for row in await repo.list_comments(post_id=post_id)]
        await _attach_my_reactions(repo, rows, current_user, "comment")
    return {"items": [_comment_response(row) for row in rows]}


@router.post("/posts/{post_id}/comments")
async def create_comment(
    post_id: int,
    payload: CommentCreateRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    body = payload.body.strip()
    if not body:
        raise _api_error(400, "BODY_REQUIRED", "댓글 내용을 입력해 주세요.")
    async with pool.acquire() as connection:
        row = await CommunityRepository(connection).create_comment(
            post_id=post_id,
            parent_comment_id=payload.parent_comment_id,
            author_user_id=int(current_user["id"]),
            body=body,
        )
    if row is None:
        # 게시글 없음/삭제 or 대댓글에 대댓글(1레벨 초과).
        raise _api_error(400, "COMMENT_TARGET_INVALID", "게시글이 없거나 대댓글에는 답글을 달 수 없습니다.")
    return _comment_response(dict(row))


@router.delete("/comments/{comment_id}")
async def delete_comment(
    comment_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        deleted = await CommunityRepository(connection).soft_delete_comment(
            comment_id=comment_id, author_user_id=int(current_user["id"])
        )
    if not deleted:
        raise _api_error(404, "COMMENT_NOT_FOUND", "댓글이 없거나 삭제 권한이 없습니다.")
    return {"deleted": True}


# ---- 반응 (좋아요/북마크) --------------------------------------------------
async def _toggle(
    connection: Any, *, target_type: str, target_id: int, author_id: int | None, user_id: int, reaction_type: str
) -> dict[str, Any]:
    if reaction_type not in _REACTION_TYPES:
        raise _api_error(400, "INVALID_REACTION", "type 은 like 또는 bookmark 이어야 합니다.")
    if author_id is None:
        raise _api_error(404, "TARGET_NOT_FOUND", "대상을 찾을 수 없습니다.")
    # 자가 좋아요 금지(어뷰징). 북마크는 본인 것도 허용.
    if reaction_type == "like" and int(author_id) == user_id:
        raise _api_error(400, "SELF_REACTION", "본인 글에는 좋아요를 누를 수 없습니다.")
    repo = CommunityRepository(connection)
    action = await repo.toggle_reaction(
        user_id=user_id, target_type=target_type, target_id=target_id, reaction_type=reaction_type
    )
    like_count = await repo.reaction_count(
        target_type=target_type, target_id=target_id, reaction_type="like"
    )
    bookmark_count = await repo.reaction_count(
        target_type=target_type, target_id=target_id, reaction_type="bookmark"
    )
    return {
        "action": action,
        "active": action == "added",
        "type": reaction_type,
        "like_count": like_count,
        "bookmark_count": bookmark_count,
    }


@router.post("/posts/{post_id}/reactions")
async def react_post(
    post_id: int,
    payload: ReactionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        author_id = await CommunityRepository(connection).get_post_author(post_id=post_id)
        return await _toggle(
            connection,
            target_type="post",
            target_id=post_id,
            author_id=author_id,
            user_id=int(current_user["id"]),
            reaction_type=payload.type,
        )


@router.post("/comments/{comment_id}/reactions")
async def react_comment(
    comment_id: int,
    payload: ReactionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        author_id = await CommunityRepository(connection).get_comment_author(comment_id=comment_id)
        return await _toggle(
            connection,
            target_type="comment",
            target_id=comment_id,
            author_id=author_id,
            user_id=int(current_user["id"]),
            reaction_type=payload.type,
        )


# ---- 신고 (임계 도달 시 자동 숨김) -----------------------------------------
async def _report(
    connection: Any, *, target_type: str, target_id: int, author_id: int | None, reporter_id: int, reason: str | None
) -> dict[str, Any]:
    if author_id is None:
        raise _api_error(404, "TARGET_NOT_FOUND", "대상을 찾을 수 없습니다.")
    hidden = await CommunityRepository(connection).create_report(
        reporter_user_id=reporter_id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        hide_threshold=REPORT_HIDE_THRESHOLD,
    )
    return {"reported": True, "hidden": hidden}


@router.post("/posts/{post_id}/report")
async def report_post(
    post_id: int,
    payload: ReportRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        author_id = await CommunityRepository(connection).get_post_author(post_id=post_id)
        return await _report(
            connection,
            target_type="post",
            target_id=post_id,
            author_id=author_id,
            reporter_id=int(current_user["id"]),
            reason=payload.reason,
        )


@router.post("/comments/{comment_id}/report")
async def report_comment(
    comment_id: int,
    payload: ReportRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        author_id = await CommunityRepository(connection).get_comment_author(comment_id=comment_id)
        return await _report(
            connection,
            target_type="comment",
            target_id=comment_id,
            author_id=author_id,
            reporter_id=int(current_user["id"]),
            reason=payload.reason,
        )
