"""Self-contained async Kiwoom REST client for the intraday capture tool.

Vendored from the proven collector the repo ships on the price-collector
branches (services/agent-worker/app/collectors/price/kiwoom/{auth,rest_client}.py
+ rate_limiter.py). Copied here — rather than imported — so this standalone
tool has no dependency on the agent-worker `app` package (and therefore no
DB import chain). Logic is unchanged.

- OAuth2 client-credentials token, cached and refreshed before expiry.
- Every domestic-stock query is a POST to a group path (default
  /api/dostk/stkinfo) with the target TR chosen by the `api-id` header.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx

KST = timezone(timedelta(hours=9))
TOKEN_PATH = "/oauth2/token"
STKINFO_PATH = "/api/dostk/stkinfo"
_REFRESH_MARGIN = timedelta(minutes=5)


class KiwoomAuthError(RuntimeError):
    pass


class KiwoomRestError(RuntimeError):
    def __init__(self, api_id: str, return_code: object, return_msg: str) -> None:
        super().__init__(f"{api_id} failed (return_code={return_code}): {return_msg}")
        self.api_id = api_id
        self.return_code = return_code
        self.return_msg = return_msg


class RateLimiter:
    """Enforces a minimum interval between outbound Kiwoom REST calls."""

    def __init__(self, min_interval_sec: float) -> None:
        self._min_interval = min_interval_sec
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def wait(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last_call
            remaining = self._min_interval - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last_call = time.monotonic()


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
                "KIWOOM_APP_KEY / KIWOOM_SECRET_KEY are required for the REST API."
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


class KiwoomRestClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        api_base: str,
        token_manager: TokenManager,
        rate_limiter: RateLimiter,
    ) -> None:
        self._http = http
        self._api_base = api_base
        self._tokens = token_manager
        self._limiter = rate_limiter

    async def request(self, api_id: str, body: dict, *, path: str = STKINFO_PATH) -> dict:
        await self._limiter.wait()
        token = await self._tokens.get_token()
        response = await self._post(path, body, api_id, token)
        if response.status_code == 401:
            # Token may have been revoked server-side; retry once with a fresh one.
            self._tokens.invalidate()
            token = await self._tokens.get_token()
            response = await self._post(path, body, api_id, token)
        response.raise_for_status()
        payload = response.json()

        return_code = payload.get("return_code", 0)
        if return_code not in (0, "0", None):
            raise KiwoomRestError(api_id, return_code, str(payload.get("return_msg", "")))
        return payload

    async def _post(
        self, path: str, body: dict, api_id: str, token: str
    ) -> httpx.Response:
        return await self._http.post(
            f"{self._api_base}{path}",
            json=body,
            headers={"authorization": f"Bearer {token}", "api-id": api_id},
        )
