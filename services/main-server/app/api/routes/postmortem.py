"""매매 의사결정 부검 — 브로커 연동 라우트 (PR-1: 자격증명 저장).

유저가 키움/토스 API 키(앱키·시크릿)를 등록하면 at-rest 암호화해 저장한다. 평문 키는
응답·로그 어디에도 남기지 않는다(마스킹조차 저장본 기준이 아니라 입력 즉시 암호화). 부검은
저널 강화 기능이라 **구독 전용**(저널과 동일 402 게이트). 체결 동기화·부검 계산은 후속 PR.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import _subscription_active, get_current_user
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
    UserBrokerCredentialRepository,
    UserTradeFillsRepository,
    UserTradePlanRepository,
    UserTradeSignalOverlayRepository,
)
from signal_alpha_data_access.crypto import CredentialCryptoError

router = APIRouter(prefix="/api/postmortem", tags=["postmortem"])

_SUPPORTED_BROKERS = {"kiwoom", "toss"}


class BrokerConnectRequest(BaseModel):
    broker: str
    app_key: str = Field(min_length=1)
    app_secret: str = Field(min_length=1)
    account_ref: str = ""
    is_mock: bool = False


@router.get("/brokers")
async def list_brokers(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        await _require_subscription(connection, int(current_user["id"]))
        rows = await UserBrokerCredentialRepository(connection).list_credentials(
            user_id=int(current_user["id"])
        )
    items = [_broker_response(dict(row)) for row in rows]
    return {"count": len(items), "items": items}


@router.post("/brokers")
async def connect_broker(
    payload: BrokerConnectRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    broker = payload.broker.strip().lower()
    if broker not in _SUPPORTED_BROKERS:
        raise _api_error(400, "UNSUPPORTED_BROKER", "지원하지 않는 증권사입니다. (키움/토스)")
    async with pool.acquire() as connection:
        await _require_subscription(connection, int(current_user["id"]))
        try:
            row = await UserBrokerCredentialRepository(connection).upsert_credential(
                user_id=int(current_user["id"]),
                broker=broker,
                account_ref=payload.account_ref.strip(),
                is_mock=payload.is_mock,
                app_key=payload.app_key,
                app_secret=payload.app_secret,
            )
        except CredentialCryptoError:
            # 서버 암호화 마스터키 미설정/오류 — 평문 저장 폴백 금지. 내부 상세는 노출하지 않는다.
            raise _api_error(
                503,
                "CREDENTIAL_ENCRYPTION_UNAVAILABLE",
                "자격증명 암호화를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
            ) from None
    return _broker_response(dict(row))


@router.delete("/brokers/{credential_id}")
async def disconnect_broker(
    credential_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        await _require_subscription(connection, int(current_user["id"]))
        deleted = await UserBrokerCredentialRepository(connection).delete_credential(
            user_id=int(current_user["id"]), credential_id=credential_id
        )
    if not deleted:
        raise _api_error(404, "BROKER_NOT_FOUND", "연동 정보를 찾을 수 없습니다.")
    # 자격증명만 삭제 — 이미 동기화된 체결 데이터는 유지(유저가 별도로 삭제 가능, spec §7).
    return {"status": "disconnected"}


@router.post("/sync")
async def request_sync(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    # main-server 는 워커 코드를 직접 호출하지 않는다 — 동기화 요청 플래그만 찍고 202.
    # 워커 러너(잦은 크론)가 요청분을 우선 처리한다(온디맨드 + 주기 증분).
    async with pool.acquire() as connection:
        await _require_subscription(connection, int(current_user["id"]))
        requested = await UserBrokerCredentialRepository(connection).request_sync(
            user_id=int(current_user["id"])
        )
    if requested == 0:
        raise _api_error(400, "NO_ACTIVE_BROKER", "먼저 증권사를 연동해 주세요.")
    return {"status": "queued", "requested": requested}


@router.get("/fills")
async def list_fills(
    stock_code: str | None = None,
    limit: int = 200,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    clean_code = stock_code.strip() if stock_code else None
    async with pool.acquire() as connection:
        await _require_subscription(connection, int(current_user["id"]))
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


def _fill_response(row: dict[str, Any]) -> dict[str, Any]:
    # 수량·가격은 정밀도 보존 위해 문자열로 내보낸다.
    return {
        "id": row["id"],
        "broker": row["broker"],
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


def _broker_response(row: dict[str, Any]) -> dict[str, Any]:
    """연동 메타만 — 키 평문/암호문은 절대 포함하지 않는다."""
    return {
        "id": row["id"],
        "broker": row["broker"],
        "account_ref": row.get("account_ref") or "",
        "is_mock": row["is_mock"],
        "status": row["status"],
        "last_synced_at": _iso(row.get("last_synced_at")),
        "last_error": row.get("last_error"),
        "created_at": _iso(row.get("created_at")),
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


async def _require_subscription(connection: Any, user_id: int) -> None:
    if not await _subscription_active(connection, user_id):
        raise _api_error(402, "SUBSCRIPTION_REQUIRED", "구독 시 매매 부검을 이용할 수 있습니다.")


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
        await _require_subscription(connection, int(current_user["id"]))
        stock = await StockRepository(connection).get_by_ticker(ticker)
        row = await UserTradePlanRepository(connection).upsert_plan(
            user_id=int(current_user["id"]),
            stock_id=int(stock["id"]) if stock else None,
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
        await _require_subscription(connection, int(current_user["id"]))
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
        await _require_subscription(connection, int(current_user["id"]))
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
) -> dict[str, Any]:
    ticker = stock_code.strip()
    async with pool.acquire() as connection:
        await _require_subscription(connection, int(current_user["id"]))
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
    return {
        "stock_code": ticker,
        "stock_name": stock.get("name"),
        "has_plan": plan is not None,
        "round_trips": round_trips,
    }


@router.get("/patterns")
async def pattern_postmortem(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        await _require_subscription(connection, int(current_user["id"]))
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
        # 소표본은 훈수 대신 억제 — 몇 건 더 필요한지 안내.
        return {"sample": patterns.get("sample", 0), "suppressed": True, "min_sample": _MIN_PATTERN_SAMPLE}
    return {"suppressed": False, **patterns}


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
