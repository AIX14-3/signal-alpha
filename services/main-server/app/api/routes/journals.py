from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import NOTICE, _subscription_active, get_current_user
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import SignalRepository, StockRepository, UserSignalRepository

_KST = ZoneInfo("Asia/Seoul")

# 차트 컨텍스트 — 작성일보다 이만큼 이전 구간부터 시리즈를 보여준다(러너 동기화 구간과 동일).
_CHART_LOOKBACK_DAYS = 30


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
        await _require_subscription(connection, int(current_user["id"]))
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
        await _require_subscription(connection, int(current_user["id"]))
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
        await _require_subscription(connection, int(current_user["id"]))
        row = await UserSignalRepository(connection).get_journal(
            user_id=int(current_user["id"]),
            journal_id=journal_id,
        )
    if row is None:
        raise _api_error(404, "JOURNAL_NOT_FOUND", "저널을 찾을 수 없습니다.")
    return _journal_response(dict(row))


@router.get("/{journal_id}/chart")
async def get_journal_chart(
    journal_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """저널 상세 차트 — 작성일 30일 전 ~ 최신 종가 시리즈 + 작성 시점 대비 등락.

    시리즈는 워커 러너가 signal_journal_chart_prices 로 동기화한 사본을 읽는다.
    아직 동기화 전이면 series 가 비고, 프론트는 "차트 준비 전"을 표시한다.
    """
    async with pool.acquire() as connection:
        await _require_subscription(connection, int(current_user["id"]))
        repository = UserSignalRepository(connection)
        row = await repository.get_journal(
            user_id=int(current_user["id"]),
            journal_id=journal_id,
        )
        if row is None:
            raise _api_error(404, "JOURNAL_NOT_FOUND", "저널을 찾을 수 없습니다.")
        created_date = _to_kst_date(row["created_at"])
        prices = await repository.list_journal_chart_prices(
            stock_id=int(row["stock_id"]),
            from_date=created_date - timedelta(days=_CHART_LOOKBACK_DAYS),
        )

    series = [
        {"trade_date": _timestamp(price["trade_date"]), "close": _number(price["close_price"])}
        for price in prices
    ]
    # 기준점 = 작성일(KST) 이하 가장 최근 거래일 종가(outcome 러너와 동일 규칙).
    base = None
    for price in prices:
        if price["trade_date"] <= created_date:
            base = price
        else:
            break
    latest = prices[-1] if prices else None
    change_pct = None
    if base is not None and latest is not None and float(base["close_price"]):
        change_pct = round(
            (float(latest["close_price"]) - float(base["close_price"]))
            / float(base["close_price"])
            * 100,
            2,
        )
    return {
        "journal": _journal_response(dict(row)),
        "chart": {
            "series": series,
            "base_trade_date": _timestamp(base["trade_date"]) if base else None,
            "base_price": _number(base["close_price"]) if base else None,
            "latest_trade_date": _timestamp(latest["trade_date"]) if latest else None,
            "latest_price": _number(latest["close_price"]) if latest else None,
            "change_pct_since_created": change_pct,
        },
        "notice": NOTICE,
    }


@router.patch("/{journal_id}")
async def update_journal(
    journal_id: int,
    payload: JournalUpdateRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        await _require_subscription(connection, int(current_user["id"]))
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
        await _require_subscription(connection, int(current_user["id"]))
        repository = UserSignalRepository(connection)
        existing = await repository.get_journal(
            user_id=int(current_user["id"]),
            journal_id=journal_id,
        )
        if existing is None:
            raise _api_error(404, "JOURNAL_NOT_FOUND", "저널을 찾을 수 없습니다.")
        await repository.delete_journal(user_id=int(current_user["id"]), journal_id=journal_id)
    return {"status": "deleted"}


async def _require_subscription(connection: Any, user_id: int) -> None:
    # 저널은 전체 구독 전용 — 조회 포함 모든 엔드포인트에서 활성 구독을 요구한다.
    if not await _subscription_active(connection, user_id):
        raise _api_error(402, "SUBSCRIPTION_REQUIRED", "구독 시 저널을 이용할 수 있습니다.")


def _journal_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "journal_id": row["id"],
        "stock_code": row["ticker"],
        "stock_name": row.get("name"),
        "final_signal_id": row.get("final_signal_id"),
        "user_view": row["user_view"],
        "memo": row.get("user_memo"),
        "tags": _json_array(row.get("tags")),
        "signal_score_at_time": _number(row.get("signal_score_at_time")),
        "signal_value_at_time": row.get("signal_value_at_time"),
        "source_agreement_at_time": row.get("source_agreement_at_time"),
        "outcomes": _json_array(row.get("outcomes")),
        "created_at": _timestamp(row.get("created_at")),
        "updated_at": _timestamp(row.get("updated_at")),
        "notice": NOTICE,
    }


def _number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _to_kst_date(value: Any) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(_KST).date()
        return value.date()
    return value


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
