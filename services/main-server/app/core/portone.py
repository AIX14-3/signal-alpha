"""포트원(아임포트) 본인인증·결제 검증 클라이언트.

- dev 모드(key/secret 미설정): 외부 호출 없이 imp_uid 로부터 결정적 모의값을 만든다.
  동일 imp_uid → 동일 phone 이므로 재가입 dedup 등 로컬/CI 테스트가 가능하다.
- real 모드: 포트원 REST API(토큰 발급 → 인증/결제 단건 조회)를 호출한다.
  httpx 는 선택 의존성이라 메서드 내부에서 지연 import 한다.

신규 기획 §3(본인인증)·§10(결제) 참조.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends

from app.core.config import Settings, get_settings


class PortOneError(RuntimeError):
    """포트원 호출/검증 실패."""


@dataclass
class IdentityResult:
    imp_uid: str
    phone: str  # 숫자만(정규화됨)
    ci: str
    name: str | None
    status: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaymentResult:
    imp_uid: str
    merchant_uid: str
    amount: int
    status: str  # 'paid' | 'ready' | 'cancelled' | 'failed'
    raw: dict[str, Any] = field(default_factory=dict)


def normalize_phone(raw: str | None) -> str:
    """핸드폰을 숫자만 남겨 정규화(유니크 일관성 보장)."""
    if not raw:
        return ""
    return "".join(ch for ch in raw if ch.isdigit())


class PortOneClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def dev_mode(self) -> bool:
        return self._settings.portone_dev_mode

    # ----- 본인인증(identity) -----
    async def verify_identity(self, imp_uid: str) -> IdentityResult:
        if self.dev_mode:
            return self._dev_identity(imp_uid)
        data = await self._get(f"/certifications/{imp_uid}")
        return IdentityResult(
            imp_uid=imp_uid,
            phone=normalize_phone(data.get("phone")),
            ci=str(data.get("unique_key") or data.get("ci") or ""),
            name=data.get("name"),
            status="certified" if data.get("certified") else "failed",
            raw=data,
        )

    # ----- 결제(payment) -----
    async def verify_payment(self, imp_uid: str) -> PaymentResult:
        if self.dev_mode:
            return self._dev_payment(imp_uid)
        data = await self._get(f"/payments/{imp_uid}")
        return PaymentResult(
            imp_uid=imp_uid,
            merchant_uid=str(data.get("merchant_uid") or ""),
            amount=int(data.get("amount") or 0),
            status=str(data.get("status") or "failed"),
            raw=data,
        )

    async def cancel_payment(self, imp_uid: str, *, reason: str = "user_cancel") -> dict[str, Any]:
        if self.dev_mode:
            return {"imp_uid": imp_uid, "status": "cancelled", "reason": reason, "dev": True}
        return await self._post("/payments/cancel", {"imp_uid": imp_uid, "reason": reason})

    # ----- dev 모드 결정적 모의값 -----
    def _dev_identity(self, imp_uid: str) -> IdentityResult:
        digest = hashlib.sha256(imp_uid.encode("utf-8")).hexdigest()
        digits = "".join(c for c in digest if c.isdigit())
        suffix = (digits + "00000000")[:8]
        return IdentityResult(
            imp_uid=imp_uid,
            phone=f"010{suffix}",
            ci=f"dev-ci-{digest[:32]}",
            name=None,
            status="certified",
            raw={"dev": True},
        )

    def _dev_payment(self, imp_uid: str) -> PaymentResult:
        return PaymentResult(
            imp_uid=imp_uid,
            merchant_uid="",  # 호출부에서 기대 merchant_uid 와 별도 검증
            amount=self._settings.subscription_price_krw,
            status="paid",
            raw={"dev": True},
        )

    # ----- real 모드 HTTP -----
    async def _token(self, client: Any) -> str:
        resp = await client.post(
            f"{self._settings.portone_api_base}/users/getToken",
            json={
                "imp_key": self._settings.portone_api_key,
                "imp_secret": self._settings.portone_api_secret,
            },
        )
        body = resp.json()
        if resp.status_code != 200 or body.get("code") != 0:
            raise PortOneError(f"token 발급 실패: {body.get('message')}")
        return body["response"]["access_token"]

    async def _get(self, path: str) -> dict[str, Any]:
        httpx = _import_httpx()
        async with httpx.AsyncClient(timeout=10.0) as client:
            token = await self._token(client)
            resp = await client.get(
                f"{self._settings.portone_api_base}{path}",
                headers={"Authorization": token},
            )
            body = resp.json()
            if resp.status_code != 200 or body.get("code") != 0:
                raise PortOneError(f"{path} 조회 실패: {body.get('message')}")
            return body["response"]

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        httpx = _import_httpx()
        async with httpx.AsyncClient(timeout=10.0) as client:
            token = await self._token(client)
            resp = await client.post(
                f"{self._settings.portone_api_base}{path}",
                headers={"Authorization": token},
                json=payload,
            )
            body = resp.json()
            if resp.status_code != 200 or body.get("code") != 0:
                raise PortOneError(f"{path} 호출 실패: {body.get('message')}")
            return body["response"]


def _import_httpx() -> Any:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise PortOneError(
            "real 모드에는 httpx 가 필요합니다. PORTONE_API_KEY/SECRET 미설정 시 dev 모드로 동작합니다."
        ) from exc
    return httpx


def get_portone_client(settings: Settings = Depends(get_settings)) -> PortOneClient:
    return PortOneClient(settings)
