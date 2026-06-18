from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.routes.auth import NOTICE, get_current_user
from app.core.database import get_database_pool
from signal_alpha_data_access.repositories import StockRepository, UserSignalRepository


WATCHLIST_LIMIT = 10

stocks_router = APIRouter(prefix="/api/stocks", tags=["stocks"])
watchlists_router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])


class WatchlistCreateRequest(BaseModel):
    stock_code: str


@stocks_router.get("/search")
async def search_stocks(
    query: str,
    limit: int = 20,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    clean_query = query.strip()
    if not clean_query:
        return {"items": []}
    async with pool.acquire() as connection:
        rows = await StockRepository(connection).search_active(clean_query, limit=min(limit, 50))
    return {"items": [_stock_response(dict(row)) for row in rows]}


@watchlists_router.get("")
async def list_watchlists(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        rows = await UserSignalRepository(connection).list_watchlist(user_id=int(current_user["id"]))
    items = [_watchlist_response(dict(row)) for row in rows]
    return {
        "limit": WATCHLIST_LIMIT,
        "count": len(items),
        "items": items,
        "notice": NOTICE,
    }


@watchlists_router.post("")
async def add_watchlist(
    payload: WatchlistCreateRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    stock_code = payload.stock_code.strip()
    async with pool.acquire() as connection:
        stock = await StockRepository(connection).get_by_ticker(stock_code)
        if stock is None:
            raise _api_error(404, "STOCK_NOT_FOUND", "종목을 찾을 수 없습니다.")
        user_signal_repository = UserSignalRepository(connection)
        existing = await user_signal_repository.get_watchlist_item(
            user_id=int(current_user["id"]),
            stock_id=int(stock["id"]),
        )
        if existing is not None:
            return _watchlist_response(dict(existing))
        count = await user_signal_repository.count_watchlist(user_id=int(current_user["id"]))
        if count >= WATCHLIST_LIMIT:
            raise _api_error(
                400,
                "WATCHLIST_LIMIT_EXCEEDED",
                "관심종목은 최대 10개까지 등록할 수 있습니다.",
            )
        watchlist = await user_signal_repository.add_watchlist(
            user_id=int(current_user["id"]),
            stock_id=int(stock["id"]),
            notification_enabled=False,
        )
    return _watchlist_response({**dict(watchlist), **dict(stock)})


@watchlists_router.delete("/{stock_code}")
async def delete_watchlist(
    stock_code: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, str]:
    async with pool.acquire() as connection:
        stock = await StockRepository(connection).get_by_ticker(stock_code)
        if stock is None:
            raise _api_error(404, "STOCK_NOT_FOUND", "종목을 찾을 수 없습니다.")
        await UserSignalRepository(connection).remove_watchlist(
            user_id=int(current_user["id"]),
            stock_id=int(stock["id"]),
        )
    return {"status": "deleted"}


def _stock_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "stock_code": row["ticker"],
        "stock_name": row["name"],
        "market": row.get("market"),
        "sector": row.get("sector"),
    }


def _watchlist_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock": _stock_response(row),
        "notification_enabled": row.get("notification_enabled", False),
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
    }


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
        },
    )
