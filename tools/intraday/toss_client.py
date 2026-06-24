"""Minimal async client for the Toss Securities Open API.

Deliberately models the Kiwoom client pair the repo already ships
(services/price-collector/app/kiwoom/auth.py + rest_client.py) so the
results of this spike translate directly into a future TossCollector.

Differences from the Kiwoom variant:
- token body uses client_id / client_secret (OAuth2 client-credentials)
- market-data calls are GET (Kiwoom uses POST + an `api-id` header)
- success is judged by HTTP status; there is no Kiwoom-style `return_code`
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx

TOKEN_PATH = "/oauth2/token"
_REFRESH_MARGIN = timedelta(minutes=5)
KST = timezone(timedelta(hours=9))


class TossAuthError(RuntimeError):
    pass


class TossApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: str) -> None:
        super().__init__(f"{method} {path} -> HTTP {status}: {body[:300]}")
        self.method = method
        self.path = path
        self.status = status
        self.body = body


class _RateLimiter:
    """Enforce a minimum gap between calls (token-bucket-lite)."""

    def __init__(self, min_interval_sec: float) -> None:
        self._min_interval = min_interval_sec
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            gap = self._min_interval - (now - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last = time.monotonic()


class TossTokenManager:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        api_base: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._http = http
        self._api_base = api_base
        self._client_id = client_id
        self._client_secret = client_secret
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
        if not self._client_id or not self._client_secret:
            raise TossAuthError(
                "TOSS_CLIENT_ID / TOSS_CLIENT_SECRET are required."
            )
        response = await self._http.post(
            f"{self._api_base}{TOKEN_PATH}",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code >= 400:
            raise TossAuthError(
                f"token request failed HTTP {response.status_code}: {response.text[:300]}"
            )
        payload = response.json()

        token = payload.get("access_token") or payload.get("token")
        if not token:
            raise TossAuthError(f"Token response did not contain a token: {payload}")

        self._token = token
        self._expires_at = _parse_expiry(payload)
        return token

    @property
    def expires_at(self) -> datetime | None:
        return self._expires_at

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = None


def _parse_expiry(payload: dict) -> datetime:
    expires_in = payload.get("expires_in")
    if expires_in:
        return datetime.now(tz=KST) + timedelta(seconds=int(expires_in))
    return datetime.now(tz=KST) + timedelta(hours=23)


class TossClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        api_base: str,
        token_manager: TossTokenManager,
        min_request_interval_sec: float = 0.25,
    ) -> None:
        self._http = http
        self._api_base = api_base
        self._tokens = token_manager
        self._limiter = _RateLimiter(min_request_interval_sec)

    async def get(self, path: str, params: dict | None = None) -> dict:
        await self._limiter.wait()
        token = await self._tokens.get_token()
        response = await self._do_get(path, params, token)
        if response.status_code == 401:
            # Token may have been revoked server-side; retry once with a fresh one.
            self._tokens.invalidate()
            token = await self._tokens.get_token()
            response = await self._do_get(path, params, token)
        if response.status_code == 429:
            # Per-group rate limit hit; back off (honour Retry-After) and retry once.
            retry_after = response.headers.get("Retry-After")
            await asyncio.sleep(float(retry_after) if retry_after else 1.0)
            response = await self._do_get(path, params, token)
        if response.status_code >= 400:
            raise TossApiError("GET", path, response.status_code, response.text)
        return response.json()

    async def _do_get(
        self, path: str, params: dict | None, token: str
    ) -> httpx.Response:
        return await self._http.get(
            f"{self._api_base}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
