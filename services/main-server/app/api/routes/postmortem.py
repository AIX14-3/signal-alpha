"""매매 의사결정 부검 — 수기 체결 입력 + 계획 + 단건/패턴 부검 라우트.

유저가 체결 내역(side=buy/sell, 일자·수량·가격)을 직접 입력하면 그걸 라운드트립으로 묶어
부검한다. 부검은 로그인 회원이면 누구나 이용한다(구독 게이트 없음). 사후확신 없이
"그때 관측 가능했던 신호"로만 판단한다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import get_current_user
from app.core.config import Settings, get_settings
from app.core.database import get_database_pool
from app.postmortem.analysis import (
    Fill,
    analyze_patterns,
    analyze_plan_vs_actual,
    build_round_trips,
)
from app.postmortem.classify import classify_roundtrip, signals_in_window
from signal_alpha_data_access.backend import (
    StockRepository,
    UserTradeFillsRepository,
    UserTradePlanRepository,
    UserTradeSignalOverlayRepository,
)

router = APIRouter(prefix="/api/postmortem", tags=["postmortem"])
logger = logging.getLogger(__name__)

_SUPPORTED_SIDES = {"buy", "sell"}


async def _postmortem_narrative(
    settings: Settings, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """워커 internal API 에 온디맨드로 복기 서술(FR-7)을 요청한다.

    LLM 판단은 워커가 수행하고(narrate + reject_advice), 여기선 결과만 붙인다. 미구성/미연결/비활성/
    권유차단은 모두 조용히 None — 부검 자체는 내러티브 없이도 성립하므로 요청을 실패시키지 않는다.
    """
    # 명시적으로 켠 배포에서만 호출(기본 off) — 워커 미배선 환경의 헛호출/로그노이즈 방지.
    if not getattr(settings, "postmortem_narrative_enabled", False):
        return None
    base_url = str(getattr(settings, "agent_worker_internal_base_url", "") or "").rstrip("/")
    token = (getattr(settings, "internal_api_token", "") or "").strip()
    if not base_url or not token:
        return None
    try:
        # 선택적 서술이라 요청 경로를 오래 막지 않도록 타임아웃을 짧게.
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                f"{base_url}/internal/postmortem/narrate",
                headers={"X-Internal-Token": token},
                json=payload,
            )
        if response.status_code >= 400 or not response.content:
            return None
        body = response.json()
        if not isinstance(body, dict):
            return None
        narrative = body.get("narrative")
        return narrative if isinstance(narrative, dict) else None
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("postmortem narrative unavailable: %s", exc)
        return None


async def _optional_stock_id(connection: Any, ticker: str) -> int | None:
    """종목코드 → stock_id. 미상장/미매핑이면 None(체결·계획은 코드만으로도 성립, overlay만 제한)."""
    stock = await StockRepository(connection).get_by_ticker(ticker)
    return int(stock["id"]) if stock else None


class FillCreateRequest(BaseModel):
    stock_code: str = Field(min_length=1)
    side: str
    filled_at: datetime
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    fee: float | None = Field(default=None, ge=0)


@router.post("/fills")
async def create_fill(
    payload: FillCreateRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """수기 체결 1건 입력(side=buy/sell, 일자·수량·가격). 종목은 코드로 매핑(미상장이면 stock_id NULL)."""
    side = payload.side.strip().lower()
    if side not in _SUPPORTED_SIDES:
        raise _api_error(400, "INVALID_SIDE", "체결 구분은 buy/sell 만 가능합니다.")
    ticker = payload.stock_code.strip()
    async with pool.acquire() as connection:
        row = await UserTradeFillsRepository(connection).insert_fill(
            user_id=int(current_user["id"]),
            stock_id=await _optional_stock_id(connection, ticker),
            ticker=ticker,
            side=side,
            filled_at=payload.filled_at,
            quantity=payload.quantity,
            price=payload.price,
            fee=payload.fee,
        )
    return _fill_response(dict(row))


@router.get("/fills")
async def list_fills(
    stock_code: str | None = None,
    limit: int = 200,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    clean_code = stock_code.strip() if stock_code else None
    async with pool.acquire() as connection:
        stock_id: int | None = None
        if clean_code:
            stock = await StockRepository(connection).get_by_ticker(clean_code)
            if stock is None:
                raise _api_error(404, "STOCK_NOT_FOUND", "종목을 찾을 수 없습니다.")
            stock_id = int(stock["id"])
        rows = await UserTradeFillsRepository(connection).list_fills(
            user_id=int(current_user["id"]),
            stock_id=stock_id,
            limit=min(max(limit, 1), 1000),
        )
    items = [_fill_response(dict(row)) for row in rows]
    return {"count": len(items), "items": items}


@router.delete("/fills/{fill_id}")
async def delete_fill(
    fill_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """오입력한 체결 삭제. 본인 소유 행만 삭제된다."""
    async with pool.acquire() as connection:
        deleted = await UserTradeFillsRepository(connection).delete_fill(
            user_id=int(current_user["id"]), fill_id=fill_id
        )
    if not deleted:
        raise _api_error(404, "FILL_NOT_FOUND", "체결 기록을 찾을 수 없습니다.")
    return {"status": "deleted"}


def _fill_response(row: dict[str, Any]) -> dict[str, Any]:
    # 수량·가격은 정밀도 보존 위해 문자열로 내보낸다.
    return {
        "id": row["id"],
        "stock_code": row["ticker"],
        "stock_id": row.get("stock_id"),
        "side": row["side"],
        "filled_at": _iso(row.get("filled_at")),
        "quantity": _num_str(row.get("quantity")),
        "price": _num_str(row.get("price")),
        "fee": _num_str(row.get("fee")),
    }


def _num_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


# ---------------------------------------------------------------------------
# PR-3a: 매매 계획(선택) + 단건/패턴 부검 (Plan vs Actual · 처분효과, 순수 계산)
# ---------------------------------------------------------------------------

# 패턴 부검 최소 표본(청산 라운드트립). 미만이면 억제한다(spec §7 소표본 경계).
_MIN_PATTERN_SAMPLE = 5


class PlanUpsertRequest(BaseModel):
    stock_code: str = Field(min_length=1)
    thesis: str = ""
    target_price: float | None = None
    stop_price: float | None = None
    sell_condition: str | None = None


@router.post("/plans")
async def upsert_plan(
    payload: PlanUpsertRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    ticker = payload.stock_code.strip()
    async with pool.acquire() as connection:
        row = await UserTradePlanRepository(connection).upsert_plan(
            user_id=int(current_user["id"]),
            stock_id=await _optional_stock_id(connection, ticker),
            ticker=ticker,
            thesis=payload.thesis,
            target_price=payload.target_price,
            stop_price=payload.stop_price,
            sell_condition=payload.sell_condition,
        )
    return _plan_response(dict(row))


@router.get("/plans")
async def list_plans(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        rows = await UserTradePlanRepository(connection).list_plans(
            user_id=int(current_user["id"])
        )
    items = [_plan_response(dict(row)) for row in rows]
    return {"count": len(items), "items": items}


@router.delete("/plans/{stock_code}")
async def delete_plan(
    stock_code: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        deleted = await UserTradePlanRepository(connection).delete_plan(
            user_id=int(current_user["id"]), ticker=stock_code.strip()
        )
    if not deleted:
        raise _api_error(404, "PLAN_NOT_FOUND", "계획을 찾을 수 없습니다.")
    return {"status": "deleted"}


@router.get("/trades/{stock_code}")
async def trade_postmortem(
    stock_code: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    ticker = stock_code.strip()
    async with pool.acquire() as connection:
        stock = await StockRepository(connection).get_by_ticker(ticker)
        if stock is None:
            raise _api_error(404, "STOCK_NOT_FOUND", "종목을 찾을 수 없습니다.")
        fill_rows = await UserTradeFillsRepository(connection).list_fills(
            user_id=int(current_user["id"]), stock_id=int(stock["id"]), limit=1000
        )
        plan_row = await UserTradePlanRepository(connection).get_plan(
            user_id=int(current_user["id"]), ticker=ticker
        )
        overlay_rows = await UserTradeSignalOverlayRepository(connection).list_by_stock(
            user_id=int(current_user["id"]), stock_id=int(stock["id"])
        )
    plan = dict(plan_row) if plan_row is not None else None
    signals = [dict(r) for r in overlay_rows]
    trips = build_round_trips(ticker, [_fill_from_row(dict(r)) for r in fill_rows])
    round_trips = []
    for trip in trips:
        pva = analyze_plan_vs_actual(trip, plan)
        window = signals_in_window(signals, trip.opened_at, trip.closed_at)
        classification = classify_roundtrip(trip, pva, window)
        round_trips.append(_roundtrip_response(trip, pva, classification, window))
    result = {
        "stock_code": ticker,
        "stock_name": stock.get("name"),
        "has_plan": plan is not None,
        "round_trips": round_trips,
        "narrative": None,
    }
    # FR-7: 온디맨드 복기 서술(워커 LLM). 부검할 라운드트립이 있을 때만 호출(빈 결과엔 헛호출 안 함).
    if round_trips:
        result["narrative"] = await _postmortem_narrative(
            settings,
            {
                "scope": "trade",
                "stock": {"stock_code": ticker, "stock_name": stock.get("name")},
                "has_plan": plan is not None,
                "round_trips": round_trips,
            },
        )
    return result


@router.get("/patterns")
async def pattern_postmortem(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        fill_rows = await UserTradeFillsRepository(connection).list_fills(
            user_id=int(current_user["id"]), limit=1000
        )
    by_ticker: dict[str, list[Fill]] = {}
    for raw in fill_rows:
        row = dict(raw)
        by_ticker.setdefault(row["ticker"], []).append(_fill_from_row(row))
    trips = [t for ticker, fills in by_ticker.items() for t in build_round_trips(ticker, fills)]
    patterns = analyze_patterns(trips)
    if patterns.get("sample", 0) < _MIN_PATTERN_SAMPLE:
        # 소표본은 훈수 대신 억제 — 몇 건 더 필요한지 안내(내러티브도 생성하지 않음).
        return {"sample": patterns.get("sample", 0), "suppressed": True, "min_sample": _MIN_PATTERN_SAMPLE}
    narrative = await _postmortem_narrative(settings, {"scope": "patterns", "patterns": patterns})
    return {"suppressed": False, "narrative": narrative, **patterns}


def _fill_from_row(row: dict[str, Any]) -> Fill:
    return Fill(
        side=row["side"],
        filled_at=row["filled_at"],
        quantity=Decimal(str(row["quantity"])),
        price=Decimal(str(row["price"])),
        fee=Decimal(str(row["fee"])) if row.get("fee") is not None else None,
    )


def _roundtrip_response(
    trip: Any,
    plan_vs_actual: dict[str, Any],
    classification: dict[str, Any],
    observed_signals: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "opened_at": _iso(trip.opened_at),
        "closed_at": _iso(trip.closed_at),
        "is_open": trip.is_open,
        "quantity": _num_str(trip.quantity),
        "avg_buy_price": _num_str(trip.avg_buy_price),
        "avg_sell_price": _num_str(trip.avg_sell_price),
        "realized_pnl_pct": trip.realized_pnl_pct,
        "holding_days": trip.holding_days,
        "plan_vs_actual": plan_vs_actual,
        # 3분류 판정 + 그때 관측 가능했던 신호(PIT). 사후 고점/저점 아님.
        "classification": classification,
        "observed_signals": [_signal_response(s) for s in observed_signals],
    }


def _signal_response(row: dict[str, Any]) -> dict[str, Any]:
    detail = row.get("detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except (ValueError, TypeError):
            detail = None
    return {
        "signal_date": _iso(row.get("signal_date")),
        "kind": row.get("kind"),
        "detail": detail,
    }


def _plan_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "stock_code": row["ticker"],
        "stock_id": row.get("stock_id"),
        "thesis": row.get("thesis") or "",
        "target_price": _num_str(row.get("target_price")),
        "stop_price": _num_str(row.get("stop_price")),
        "sell_condition": row.get("sell_condition"),
        "planned_at": _iso(row.get("planned_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
