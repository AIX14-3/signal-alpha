from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.routes.auth import NOTICE, get_current_user
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import (
    StockNewsRepository,
    StockPriceRepository,
    StockRepository,
    UserSignalRepository,
)


# 신규 기획: 관심종목은 회원/유료 무관 무제한. 한도 검사를 두지 않는다.

stocks_router = APIRouter(prefix="/api/stocks", tags=["stocks"])
watchlists_router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])
news_router = APIRouter(prefix="/api/news", tags=["news"])

# 뉴스 집계 최근 윈도우 한도: 1시간 ~ 30일. 과도한 값으로 인한 오남용 방어.
NEWS_SUMMARY_RECENT_HOURS_MIN = 1
NEWS_SUMMARY_RECENT_HOURS_MAX = 720

# 가격 차트 조회 창 한도: 7일 ~ 120일, 기본 90일.
# 상한(120)은 발행 러너 sync_stock_prices 의 동기화 창(_PRICE_LOOKBACK_DAYS=120)과 맞춘다 —
# 더 크게 허용하면 아직 발행되지 않은 과거 구간이 조용히 잘린 series 로 나간다.
PRICE_DAYS_MIN = 7
PRICE_DAYS_MAX = 120
PRICE_DAYS_DEFAULT = 90
_KST = ZoneInfo("Asia/Seoul")


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
    """종목별 최신 뉴스 목록 + 건수 + 다이제스트(공개). 워커 뉴스 데몬이 적재·요약."""
    ticker = stock_code.strip()
    async with pool.acquire() as connection:
        repository = StockNewsRepository(connection)
        rows = await repository.list_by_ticker(ticker, limit=min(max(limit, 1), 50))
        count = await repository.count_by_ticker(ticker)
        digest_row = await repository.get_digest_by_ticker(ticker)
    return {
        "count": count,
        "items": [_news_response(dict(row)) for row in rows],
        # 종목 뉴스 흐름 한 줄(LLM). 없으면 null → 프론트는 블록 생략.
        "digest": _digest_response(dict(digest_row)) if digest_row is not None else None,
    }


@stocks_router.get("/{stock_code}/prices")
async def list_stock_prices(
    stock_code: str,
    days: int = PRICE_DAYS_DEFAULT,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """종목별 일봉 종가 시리즈(공개) — 홈 '실시간 분석 종목' 아코디언 차트.

    발행 러너(sync_stock_prices)가 수집 DB ohlcv_data 에서 backend stock_price_daily 로
    동기화한 종가만 읽는다(백엔드는 수집 DB 에 접속하지 않음). 미동기화 종목은 series 가
    비고, 프론트는 "차트 준비 중"을 표시한다.
    """
    ticker = stock_code.strip()
    window = min(max(days, PRICE_DAYS_MIN), PRICE_DAYS_MAX)
    from_date = datetime.now(_KST).date() - timedelta(days=window)
    async with pool.acquire() as connection:
        stock = await StockRepository(connection).get_by_ticker(ticker)
        if stock is None:
            raise _api_error(404, "STOCK_NOT_FOUND", "종목을 찾을 수 없습니다.")
        rows = await StockPriceRepository(connection).list_daily_close(
            stock_id=int(stock["id"]),
            from_date=from_date,
        )
    series = [
        {
            "trade_date": _date_iso(row["trade_date"]),
            "close": _number(row["close_price"]),
        }
        for row in rows
    ]
    latest = series[-1] if series else None
    return {
        "stock": {"stock_code": stock["ticker"], "stock_name": stock["name"]},
        "series": series,
        "latest_trade_date": latest["trade_date"] if latest else None,
        "latest_price": latest["close"] if latest else None,
        "notice": NOTICE,
    }


@news_router.get("/summary")
async def news_summary(
    recent_hours: int = 24,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """전역 뉴스 집계(공개) — 토스식 '뉴스 N건을 분석한 시그널' 헤더 데이터 공급원.

    recent_hours 로 recent_articles 집계 창을 조절한다(기본 24h, 1h~30d 클램프).
    """
    window = min(
        max(recent_hours, NEWS_SUMMARY_RECENT_HOURS_MIN),
        NEWS_SUMMARY_RECENT_HOURS_MAX,
    )
    async with pool.acquire() as connection:
        data = await StockNewsRepository(connection).summary(recent_hours=window)
    return {**_news_summary_response(data), "recent_hours": window}


@news_router.get("/recent")
async def recent_news(
    limit: int = 30,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """전역 최신 뉴스 목록(공개) — 홈 2-pane 좌측 '뉴스 피드'(종목명 포함)."""
    async with pool.acquire() as connection:
        rows = await StockNewsRepository(connection).list_recent(limit=min(max(limit, 1), 50))
    return {"items": [_recent_news_response(dict(row)) for row in rows]}


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


def _recent_news_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock_code": row.get("stock_code"),
        "stock_name": row.get("stock_name"),
        **_news_response(row),
    }


def _digest_response(row: dict[str, Any]) -> dict[str, Any]:
    generated_at = row.get("generated_at")
    return {
        "text": row.get("digest_text"),
        "model": row.get("model"),
        "article_count": int(row.get("article_count") or 0),
        "generated_at": (
            generated_at.isoformat() if hasattr(generated_at, "isoformat") else generated_at
        ),
    }


def _news_summary_response(data: dict[str, Any]) -> dict[str, Any]:
    latest = data.get("latest_collected_at")
    return {
        "total_articles": int(data.get("total_articles") or 0),
        "stock_count": int(data.get("stock_count") or 0),
        "recent_articles": int(data.get("recent_articles") or 0),
        "latest_collected_at": latest.isoformat() if hasattr(latest, "isoformat") else latest,
        "notice": NOTICE,
    }


def _watchlist_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock": _stock_response(row),
        "notification_enabled": row.get("notification_enabled", False),
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
    }


def _date_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
        },
    )
