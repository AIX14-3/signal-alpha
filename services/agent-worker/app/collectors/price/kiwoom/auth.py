"""OAuth access-token management for the Kiwoom REST API.

POST {base}/oauth2/token with the App Key/Secret issues a bearer token.
The token is cached and refreshed shortly before its expiry.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx

from app.collectors.price.market_hours import KST

TOKEN_PATH = "/oauth2/token"
_REFRESH_MARGIN = timedelta(minutes=5)


class KiwoomAuthError(RuntimeError):
    pass


class TokenManager:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        api_base: str,
        app_key: str,
        app_secret: str,
    ) -> None:
        self._http = http
        self._api_base = api_base
        self._app_key = app_key
        self._app_secret = app_secret
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            if self._token and self._expires_at:
                if datetime.now(tz=KST) < self._expires_at - _REFRESH_MARGIN:
                    return self._token
            return await self._issue_token()

    async def _issue_token(self) -> str:
        if not self._app_key or not self._app_secret:
            raise KiwoomAuthError(
                "KIWOOM_APP_KEY / KIWOOM_APP_SECRET are required for the REST API."
            )
        response = await self._http.post(
            f"{self._api_base}{TOKEN_PATH}",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "secretkey": self._app_secret,
            },
        )
        response.raise_for_status()
        payload = response.json()

        token = payload.get("token") or payload.get("access_token")
        if not token:
            raise KiwoomAuthError(f"Token response did not contain a token: {payload}")

        self._token = token
        self._expires_at = _parse_expiry(payload)
        return token

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = None


def _parse_expiry(payload: dict) -> datetime:
    # Kiwoom returns expires_dt as "YYYYMMDDHHMMSS" (KST); some OAuth
    # responses use expires_in seconds instead, so accept both.
    expires_dt = payload.get("expires_dt")
    if expires_dt:
        return datetime.strptime(str(expires_dt), "%Y%m%d%H%M%S").replace(tzinfo=KST)
    expires_in = payload.get("expires_in")
    if expires_in:
        return datetime.now(tz=KST) + timedelta(seconds=int(expires_in))
    return datetime.now(tz=KST) + timedelta(hours=23)
