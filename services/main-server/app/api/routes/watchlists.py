from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.routes.auth import NOTICE, get_current_user
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import (
    StockNewsRepository,
    StockRepository,
    UserSignalRepository,
)


# 신규 기획: 관심종목은 회원/유료 무관 무제한. 한도 검사를 두지 않는다.

stocks_router = APIRouter(prefix="/api/stocks", tags=["stocks"])
watchlists_router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])
news_router = APIRouter(prefix="/api/news", tags=["news"])

# 집계 창 한도: 1시간 ~ 30일. 과도한 window 로 인한 스캔 낭비/오남용 방어.
NEWS_SUMMARY_WINDOW_MIN_HOURS = 1
NEWS_SUMMARY_WINDOW_MAX_HOURS = 720


class WatchlistCreateRequest(BaseModel):
    stock_code: str


@stocks_router.get("")
async def list_stocks(
    limit: int = 100,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """활성 종목 목록(공개). 검색창 placeholder 회전용 종목명 소스로 사용."""
    async with pool.acquire() as connection:
        rows = await StockRepository(connection).list_active(limit=min(limit, 200))
    return {"items": [_stock_response(dict(row)) for row in rows]}


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


@stocks_router.get("/{stock_code}/news")
async def list_stock_news(
    stock_code: str,
    limit: int = 20,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """종목별 최신 뉴스 목록 + 건수(공개). 워커 뉴스 데몬이 api.stock_news 로 적재."""
    ticker = stock_code.strip()
    async with pool.acquire() as connection:
        repository = StockNewsRepository(connection)
        rows = await repository.list_by_ticker(ticker, limit=min(max(limit, 1), 50))
        count = await repository.count_by_ticker(ticker)
    return {
        "count": count,
        "items": [_news_response(dict(row)) for row in rows],
    }


@news_router.get("/summary")
async def news_summary(
    window_hours: int = 24,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """전역 뉴스 집계(공개) — 토스식 "뉴스 N건을 분석한 시그널" 헤더 공급원.

    recent = window_hours 내 published_at 건수(헤드라인), total = 전체 적재 건수,
    recent_stock_count = 그 창에서 뉴스가 있는 종목 수.
    """
    window = min(
        max(window_hours, NEWS_SUMMARY_WINDOW_MIN_HOURS),
        NEWS_SUMMARY_WINDOW_MAX_HOURS,
    )
    async with pool.acquire() as connection:
        summary = await StockNewsRepository(connection).summary(window_hours=window)
    return {**summary, "window_hours": window}


@watchlists_router.get("")
async def list_watchlists(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        rows = await UserSignalRepository(connection).list_watchlist(user_id=int(current_user["id"]))
    items = [_watchlist_response(dict(row)) for row in rows]
    return {
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


def _news_response(row: dict[str, Any]) -> dict[str, Any]:
    published_at = row.get("published_at")
    return {
        "title": row.get("title"),
        "summary": row.get("summary"),
        "url": row.get("url"),
        "press": row.get("press"),
        "source": row.get("source"),
        "published_at": published_at.isoformat() if published_at else None,
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
