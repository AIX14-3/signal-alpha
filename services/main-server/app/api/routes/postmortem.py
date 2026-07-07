"""매매 의사결정 부검 — 브로커 연동 라우트 (PR-1: 자격증명 저장).

유저가 키움/토스 API 키(앱키·시크릿)를 등록하면 at-rest 암호화해 저장한다. 평문 키는
응답·로그 어디에도 남기지 않는다(마스킹조차 저장본 기준이 아니라 입력 즉시 암호화). 부검은
저널 강화 기능이라 **구독 전용**(저널과 동일 402 게이트). 체결 동기화·부검 계산은 후속 PR.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import _subscription_active, get_current_user
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import (
    StockRepository,
    UserBrokerCredentialRepository,
    UserTradeFillsRepository,
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
