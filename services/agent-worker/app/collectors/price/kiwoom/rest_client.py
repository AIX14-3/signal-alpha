"""Thin async client for Kiwoom REST API calls.

Every domestic-stock query is a POST to a group path (e.g. /api/dostk/stkinfo)
with the target API distinguished by the ``api-id`` header (ka10001, ka10059…).
"""

from __future__ import annotations

import httpx

from app.collectors.price.rate_limiter import RateLimiter
from app.collectors.price.kiwoom.auth import TokenManager

STKINFO_PATH = "/api/dostk/stkinfo"


class KiwoomRestError(RuntimeError):
    def __init__(self, api_id: str, return_code: object, return_msg: str) -> None:
        super().__init__(f"{api_id} failed (return_code={return_code}): {return_msg}")
        self.api_id = api_id
        self.return_code = return_code
        self.return_msg = return_msg


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
        response = await self._http.post(
            f"{self._api_base}{path}",
            json=body,
            headers={
                "authorization": f"Bearer {token}",
                "api-id": api_id,
            },
        )
        if response.status_code == 401:
            # Token may have been revoked server-side; retry once with a fresh one.
            self._tokens.invalidate()
            token = await self._tokens.get_token()
            response = await self._http.post(
                f"{self._api_base}{path}",
                json=body,
                headers={
                    "authorization": f"Bearer {token}",
                    "api-id": api_id,
                },
            )
        response.raise_for_status()
        payload = response.json()

        return_code = payload.get("return_code", 0)
        if return_code not in (0, "0", None):
            raise KiwoomRestError(api_id, return_code, str(payload.get("return_msg", "")))
        return payload
