"""포트원 V2 본인인증·결제 검증 클라이언트.

V2 REST: 베이스 https://api.portone.io, 인증 헤더 `Authorization: PortOne {API_SECRET}`.
- 본인인증 조회: GET /identity-verifications/{identityVerificationId}
    → status="VERIFIED", verifiedCustomer.{phoneNumber, ci, name}
- 결제 조회:     GET /payments/{paymentId}  → status="PAID", amount.total
- 결제 취소:     POST /payments/{paymentId}/cancel  { reason }

식별자(identityVerificationId / paymentId)는 프론트(가맹점)가 생성해 SDK에 넘기고,
백엔드는 같은 식별자로 V2 API 를 조회해 검증한다.

dev 모드(api_secret 미설정): 외부 호출 없이 식별자로부터 결정적 모의값 생성.
httpx 는 선택 의존성이라 메서드 내부에서 지연 import 한다.
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
    id: str  # identityVerificationId
    phone: str  # 숫자만(정규화됨)
    ci: str
    name: str | None
    status: str  # 'certified' | 'failed'
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaymentResult:
    payment_id: str
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
    async def verify_identity(self, identity_verification_id: str) -> IdentityResult:
        if self.dev_mode:
            return self._dev_identity(identity_verification_id)
        data = await self._get(f"/identity-verifications/{identity_verification_id}")
        customer = data.get("verifiedCustomer") or {}
        return IdentityResult(
            id=identity_verification_id,
            phone=normalize_phone(customer.get("phoneNumber")),
            ci=str(customer.get("ci") or ""),
            name=customer.get("name"),
            status="certified" if data.get("status") == "VERIFIED" else "failed",
            raw=data,
        )

    # ----- 결제(payment) -----
    async def verify_payment(self, payment_id: str) -> PaymentResult:
        if self.dev_mode:
            return self._dev_payment(payment_id)
        data = await self._get(f"/payments/{payment_id}")
        amount = (data.get("amount") or {}).get("total") or 0
        status = "paid" if data.get("status") == "PAID" else str(data.get("status") or "failed").lower()
        return PaymentResult(payment_id=payment_id, amount=int(amount), status=status, raw=data)

    async def cancel_payment(
        self, payment_id: str, *, reason: str = "user_cancel", amount: int | None = None
    ) -> dict[str, Any]:
        """결제 취소. amount 지정 시 부분취소(일할 환불), 미지정 시 전액취소."""
        if self.dev_mode:
            return {
                "paymentId": payment_id,
                "status": "CANCELLED",
                "reason": reason,
                "amount": amount,
                "dev": True,
            }
        payload: dict[str, Any] = {"reason": reason}
        if amount is not None:
            payload["amount"] = amount
        return await self._post(f"/payments/{payment_id}/cancel", payload)

    # ----- dev 모드 결정적 모의값 -----
    def _dev_identity(self, identity_verification_id: str) -> IdentityResult:
        digest = hashlib.sha256(identity_verification_id.encode("utf-8")).hexdigest()
        digits = "".join(c for c in digest if c.isdigit())
        suffix = (digits + "00000000")[:8]
        return IdentityResult(
            id=identity_verification_id,
            phone=f"010{suffix}",
            ci=f"dev-ci-{digest[:32]}",
            name=None,
            status="certified",
            raw={"dev": True},
        )

    def _dev_payment(self, payment_id: str) -> PaymentResult:
        return PaymentResult(
            payment_id=payment_id,
            amount=self._settings.subscription_price_krw,
            status="paid",
            raw={"dev": True},
        )

    # ----- real 모드 HTTP (V2) -----
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"PortOne {self._settings.portone_api_secret}"}

    async def _get(self, path: str) -> dict[str, Any]:
        httpx = _import_httpx()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self._settings.portone_api_base}{path}", headers=self._headers()
            )
            return self._json(resp, path)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        httpx = _import_httpx()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._settings.portone_api_base}{path}",
                headers=self._headers(),
                json=payload,
            )
            return self._json(resp, path)

    @staticmethod
    def _json(resp: Any, path: str) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code // 100 != 2:
            message = body.get("message") if isinstance(body, dict) else None
            raise PortOneError(f"{path} 실패({resp.status_code}): {message}")
        return body if isinstance(body, dict) else {}


def _import_httpx() -> Any:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise PortOneError(
            "real 모드에는 httpx 가 필요합니다. PORTONE_API_SECRET 미설정 시 dev 모드로 동작합니다."
        ) from exc
    return httpx


def get_portone_client(settings: Settings = Depends(get_settings)) -> PortOneClient:
    return PortOneClient(settings)
