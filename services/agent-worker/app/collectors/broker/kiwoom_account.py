"""키움증권 계좌 체결내역 클라이언트.

키움 REST(openapi.kiwoom.com): 앱키/시크릿 → 토큰(client_credentials), 계좌 TR 은
`/api/dostk/acnt` 경로에 api-id 헤더로 호출. 계좌별 주문체결내역 상세 TR(kt00007 계열)로
체결 목록을 받는다. 실전/모의 호스트 분리(모의계좌 키는 별도 발급).

정규화(`normalize_kiwoom_rows`)는 순수·방어적이라 faked 로 테스트한다. HTTP 흐름
(`fetch_fills`)은 문서 기반이며 ⚠️ 실키 없이는 라이브 미검증 — api-id/응답 필드명(한글 키)은
실연동 시 조정될 수 있다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from app.collectors.broker.base import NormalizedFill, normalize_side, to_decimal

_HOST_REAL = "https://api.kiwoom.com"
_HOST_MOCK = "https://mockapi.kiwoom.com"
_ACCT_PATH = "/api/dostk/acnt"
_FILLS_TR = "kt00007"  # 계좌별주문체결내역상세요청(추정) — 실연동 시 확인
_BUY = {"2", "매수", "buy", "+매수"}
_SELL = {"1", "매도", "sell", "-매도"}


def normalize_kiwoom_rows(payload: dict[str, Any]) -> list[NormalizedFill]:
    """키움 계좌 체결 TR 응답 → NormalizedFill 목록.

    행 리스트(출력 키는 TR마다 다름 — 여러 후보 키를 방어적으로 탐색)의 각 행이 1 fill.
    체결번호+주문번호로 broker_fill_id, 종목코드/매도수구분/체결시간/체결량/체결가 매핑.
    필수 누락·미체결(수량 0) 행은 skip.
    """
    rows = _extract_rows(payload)
    fills: list[NormalizedFill] = []
    for row in rows:
        ticker = _clean_ticker(row.get("stk_cd") or row.get("종목코드") or row.get("code"))
        side = normalize_side(
            row.get("io_tp_nm") or row.get("sell_tp") or row.get("매도수구분"),
            buy_tokens=_BUY,
            sell_tokens=_SELL,
        )
        fill_id = str(
            row.get("cntr_no") or row.get("체결번호") or row.get("ord_no") or ""
        ).strip()
        qty = to_decimal(row.get("cntr_qty") or row.get("체결량") or row.get("qty"))
        price = to_decimal(row.get("cntr_pric") or row.get("체결가") or row.get("price"))
        filled_at = _parse_kiwoom_ts(
            row.get("cntr_tm") or row.get("체결시간") or row.get("ord_tm"),
            row.get("cntr_dt") or row.get("체결일자") or row.get("ord_dt"),
        )
        if not ticker or side is None or not fill_id or qty is None or price is None:
            continue
        if qty <= 0:
            continue
        fills.append(
            NormalizedFill(
                broker_fill_id=fill_id,
                ticker=ticker,
                side=side,
                filled_at=filled_at,
                quantity=qty,
                price=price,
                fee=to_decimal(row.get("cmsn") or row.get("수수료")),
            )
        )
    return fills


def _extract_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("acnt_ord_cntr_dtl", "output", "rows", "data", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _clean_ticker(value: Any) -> str:
    # 키움은 종목코드에 접두 'A' 를 붙이기도 한다(A005930).
    t = str(value or "").strip()
    return t[1:] if t[:1] == "A" and t[1:].isdigit() else t


def _parse_kiwoom_ts(time_val: Any, date_val: Any) -> datetime | None:
    from datetime import timezone
    from zoneinfo import ZoneInfo

    date_s = str(date_val or "").strip()
    time_s = str(time_val or "").strip().replace(":", "")
    if len(date_s) != 8 or not date_s.isdigit():
        return None
    time_s = (time_s + "000000")[:6] if time_s.isdigit() else "000000"
    try:
        naive = datetime.strptime(date_s + time_s, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=ZoneInfo("Asia/Seoul")).astimezone(timezone.utc)


class KiwoomAccountClient:
    """키움 계좌 체결 조회. HTTP 는 문서 기반·라이브 미검증(정규화만 테스트됨)."""

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
        host = _HOST_MOCK if is_mock else _HOST_REAL
        http = self._http or httpx.AsyncClient(base_url=host, timeout=10.0)
        owns = self._http is None
        try:
            token = await self._token(http, app_key, app_secret)
            headers = {"authorization": f"Bearer {token}", "api-id": _FILLS_TR}
            body: dict[str, Any] = {"qry_tp": "1"}
            if since is not None:
                body["strt_dt"] = since.strftime("%Y%m%d")
            resp = await http.post(_ACCT_PATH, headers=headers, json=body)
            resp.raise_for_status()
            return normalize_kiwoom_rows(resp.json())
        finally:
            if owns:
                await http.aclose()

    async def _token(self, http: httpx.AsyncClient, app_key: str, app_secret: str) -> str:
        resp = await http.post(
            "/oauth2/token",
            json={"grant_type": "client_credentials", "appkey": app_key, "secretkey": app_secret},
        )
        resp.raise_for_status()
        return str(resp.json().get("token") or resp.json().get("access_token") or "")
