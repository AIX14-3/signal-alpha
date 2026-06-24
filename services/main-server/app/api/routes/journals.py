from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import NOTICE, get_current_user
from app.core.database import get_database_pool
from signal_alpha_data_access.repositories import SignalRepository, StockRepository, UserSignalRepository


ALLOWED_USER_VIEWS = {"watch", "research_more", "not_relevant"}
FORBIDDEN_USER_VIEWS = {"buy", "sell", "hold", "entry", "exit", "target_price"}

router = APIRouter(prefix="/api/journals", tags=["journals"])


class JournalCreateRequest(BaseModel):
    stock_code: str
    final_signal_id: int
    user_view: str
    memo: str | None = None
    tags: list[str] = Field(default_factory=list)


class JournalUpdateRequest(BaseModel):
    user_view: str | None = None
    memo: str | None = None
    tags: list[str] | None = None


@router.get("")
async def list_journals(
    stock_code: str | None = None,
    limit: int = 20,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    clean_stock_code = stock_code.strip() if stock_code else None
    async with pool.acquire() as connection:
        rows = await UserSignalRepository(connection).list_journals(
            user_id=int(current_user["id"]),
            stock_code=clean_stock_code,
            limit=min(max(limit, 1), 100),
        )
    items = [_journal_response(dict(row)) for row in rows]
    return {
        "count": len(items),
        "items": items,
        "notice": NOTICE,
    }


@router.post("")
async def create_journal(
    payload: JournalCreateRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    user_view = _validate_user_view(payload.user_view)
    stock_code = payload.stock_code.strip()
    async with pool.acquire() as connection:
        stock = await StockRepository(connection).get_by_ticker(stock_code)
        if stock is None:
            raise _api_error(404, "STOCK_NOT_FOUND", "종목을 찾을 수 없습니다.")
        signal = await SignalRepository(connection).get_detail_by_id(payload.final_signal_id)
        if signal is None:
            raise _api_error(404, "SIGNAL_NOT_FOUND", "시그널을 찾을 수 없습니다.")
        if int(signal["stock_id"]) != int(stock["id"]):
            raise _api_error(400, "SIGNAL_STOCK_MISMATCH", "시그널과 종목이 일치하지 않습니다.")
        row = await UserSignalRepository(connection).create_journal_entry(
            user_id=int(current_user["id"]),
            stock_id=int(stock["id"]),
            final_signal_id=int(signal["id"]),
            user_view=user_view,
            user_memo=payload.memo,
            tags=_clean_tags(payload.tags),
            signal_score_at_time=signal.get("final_score"),
            signal_value_at_time=signal.get("signal"),
            source_agreement_at_time=signal.get("source_agreement"),
        )
    return _journal_response(dict(row))


@router.get("/{journal_id}")
async def get_journal(
    journal_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        row = await UserSignalRepository(connection).get_journal(
            user_id=int(current_user["id"]),
            journal_id=journal_id,
        )
    if row is None:
        raise _api_error(404, "JOURNAL_NOT_FOUND", "저널을 찾을 수 없습니다.")
    return _journal_response(dict(row))


@router.patch("/{journal_id}")
async def update_journal(
    journal_id: int,
    payload: JournalUpdateRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        repository = UserSignalRepository(connection)
        existing = await repository.get_journal(
            user_id=int(current_user["id"]),
            journal_id=journal_id,
        )
        if existing is None:
            raise _api_error(404, "JOURNAL_NOT_FOUND", "저널을 찾을 수 없습니다.")
        row = await repository.update_journal(
            user_id=int(current_user["id"]),
            journal_id=journal_id,
            user_view=_validate_user_view(payload.user_view or existing["user_view"]),
            user_memo=payload.memo if payload.memo is not None else existing.get("user_memo"),
            tags=_clean_tags(payload.tags if payload.tags is not None else _json_array(existing.get("tags"))),
        )
    if row is None:
        raise _api_error(404, "JOURNAL_NOT_FOUND", "저널을 찾을 수 없습니다.")
    return _journal_response(dict(row))


@router.delete("/{journal_id}")
async def delete_journal(
    journal_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, str]:
    async with pool.acquire() as connection:
        repository = UserSignalRepository(connection)
        existing = await repository.get_journal(
            user_id=int(current_user["id"]),
            journal_id=journal_id,
        )
        if existing is None:
            raise _api_error(404, "JOURNAL_NOT_FOUND", "저널을 찾을 수 없습니다.")
        await repository.delete_journal(user_id=int(current_user["id"]), journal_id=journal_id)
    return {"status": "deleted"}


def _journal_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "journal_id": row["id"],
        "stock_code": row["ticker"],
        "stock_name": row.get("name"),
        "final_signal_id": row.get("final_signal_id"),
        "user_view": row["user_view"],
        "memo": row.get("user_memo"),
        "tags": _json_array(row.get("tags")),
        "created_at": _timestamp(row.get("created_at")),
        "updated_at": _timestamp(row.get("updated_at")),
        "notice": NOTICE,
    }


def _validate_user_view(value: str) -> str:
    clean_value = value.strip()
    if clean_value in FORBIDDEN_USER_VIEWS or clean_value not in ALLOWED_USER_VIEWS:
        raise _api_error(
            400,
            "INVALID_USER_VIEW",
            "저널 판단 값은 watch, research_more, not_relevant 중 하나여야 합니다.",
        )
    return clean_value


def _clean_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in tags:
        text = tag.strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned[:10]


def _json_array(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return list(value)


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
