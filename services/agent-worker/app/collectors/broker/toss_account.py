"""토스증권 계좌 체결내역 클라이언트.

문서(https://developers.tossinvest.com/docs/order-history): OAuth2 Client Credentials 토큰 +
`GET /api/v1/orders`(status=CLOSED, from/to, cursor, limit, accountSeq) → 주문/부분체결.
계좌 엔드포인트는 `X-Tossinvest-Account` 헤더 필요. symbol=KRX 6자리.

정규화(`normalize_toss_orders`)는 순수·방어적이라 faked 로 테스트한다. HTTP 흐름(`fetch_fills`)은
문서 기반이며 ⚠️ 실키 없이는 라이브 미검증 — 응답 필드명은 실연동 시 조정될 수 있다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from app.collectors.broker.base import NormalizedFill, normalize_side, to_decimal

_BASE_URL = "https://openapi.tossinvest.com"
_BUY = {"buy", "bid", "매수", "2"}
_SELL = {"sell", "ask", "매도", "1"}


def normalize_toss_orders(payload: dict[str, Any]) -> list[NormalizedFill]:
    """토스 주문 목록 payload → NormalizedFill 목록.

    주문별 executions 배열의 각 체결이 1 fill. 필수 필드(체결id·수량·가격·시각) 누락 시 그
    체결만 skip(방어적). broker_fill_id = orderId:executionId.
    """
    fills: list[NormalizedFill] = []
    for order in payload.get("orders") or payload.get("data") or []:
        order_id = str(order.get("orderId") or order.get("id") or "").strip()
        ticker = str(order.get("symbol") or order.get("code") or "").strip()
        side = normalize_side(
            order.get("side") or order.get("orderType"), buy_tokens=_BUY, sell_tokens=_SELL
        )
        if not order_id or not ticker or side is None:
            continue
        for ex in order.get("executions") or order.get("fills") or []:
            ex_id = str(ex.get("executionId") or ex.get("seq") or "").strip()
            qty = to_decimal(ex.get("quantity") or ex.get("qty"))
            price = to_decimal(ex.get("price") or ex.get("executedPrice"))
            filled_at = _parse_ts(ex.get("executedAt") or ex.get("tradedAt"))
            if not ex_id or qty is None or price is None or filled_at is None or qty <= 0:
                continue
            fills.append(
                NormalizedFill(
                    broker_fill_id=f"{order_id}:{ex_id}",
                    ticker=ticker,
                    side=side,
                    filled_at=filled_at,
                    quantity=qty,
                    price=price,
                    fee=to_decimal(ex.get("fee")),
                )
            )
    return fills


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class TossAccountClient:
    """토스 계좌 체결 조회. HTTP 는 문서 기반·라이브 미검증(정규화만 테스트됨)."""

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http

    async def fetch_fills(
        self,
        *,
        app_key: str,
        app_secret: str,
        account_ref: str,
        is_mock: bool,
        since: datetime | None,
    ) -> list[NormalizedFill]:
        http = self._http or httpx.AsyncClient(base_url=_BASE_URL, timeout=10.0)
        owns = self._http is None
        try:
            token = await self._token(http, app_key, app_secret)
            headers = {"Authorization": f"Bearer {token}", "X-Tossinvest-Account": account_ref}
            params: dict[str, Any] = {"status": "CLOSED", "limit": 100}
            if since is not None:
                params["from"] = since.date().isoformat()
            resp = await http.get("/api/v1/orders", headers=headers, params=params)
            resp.raise_for_status()
            return normalize_toss_orders(resp.json())
        finally:
            if owns:
                await http.aclose()

    async def _token(self, http: httpx.AsyncClient, app_key: str, app_secret: str) -> str:
        resp = await http.post(
            "/api/v1/oauth/token",
            data={"grant_type": "client_credentials", "clientId": app_key, "clientSecret": app_secret},
        )
        resp.raise_for_status()
        return str(resp.json().get("access_token") or resp.json().get("accessToken") or "")
