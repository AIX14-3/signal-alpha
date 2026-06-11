import asyncio
import json

import httpx
import pytest

from app.core.rate_limiter import RateLimiter
from app.kiwoom.auth import KiwoomAuthError, TokenManager
from app.kiwoom.rest_client import KiwoomRestClient, KiwoomRestError

BASE = "https://mockapi.kiwoom.com"


def make_client(handler) -> tuple[KiwoomRestClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tokens = TokenManager(http=http, api_base=BASE, app_key="key", app_secret="secret")
    client = KiwoomRestClient(
        http=http,
        api_base=BASE,
        token_manager=tokens,
        rate_limiter=RateLimiter(0),
    )
    return client, http


def test_request_issues_token_then_calls_api():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            body = json.loads(request.content)
            seen["grant_type"] = body["grant_type"]
            return httpx.Response(
                200, json={"return_code": 0, "token": "T123", "expires_dt": "20260906235959"}
            )
        seen["api_id"] = request.headers.get("api-id")
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"return_code": 0, "cur_prc": "+74300"})

    async def run():
        client, http = make_client(handler)
        async with http:
            return await client.request("ka10001", {"stk_cd": "005930"})

    payload = asyncio.run(run())

    assert payload["cur_prc"] == "+74300"
    assert seen["grant_type"] == "client_credentials"
    assert seen["api_id"] == "ka10001"
    assert seen["auth"] == "Bearer T123"


def test_token_reused_until_expiry():
    counter = {"tokens": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            counter["tokens"] += 1
            return httpx.Response(200, json={"token": "T", "expires_dt": "20991231235959"})
        return httpx.Response(200, json={"return_code": 0})

    async def run():
        client, http = make_client(handler)
        async with http:
            await client.request("ka10001", {"stk_cd": "005930"})
            await client.request("ka10001", {"stk_cd": "000660"})

    asyncio.run(run())

    assert counter["tokens"] == 1


def test_retry_once_on_401():
    counter = {"tokens": 0, "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            counter["tokens"] += 1
            return httpx.Response(200, json={"token": f"T{counter['tokens']}", "expires_in": 3600})
        counter["calls"] += 1
        if counter["calls"] == 1:
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"return_code": 0})

    async def run():
        client, http = make_client(handler)
        async with http:
            return await client.request("ka10001", {"stk_cd": "005930"})

    payload = asyncio.run(run())

    assert payload["return_code"] == 0
    assert counter["tokens"] == 2
    assert counter["calls"] == 2


def test_nonzero_return_code_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "T", "expires_in": 3600})
        return httpx.Response(200, json={"return_code": 3, "return_msg": "조회 오류"})

    async def run():
        client, http = make_client(handler)
        async with http:
            await client.request("ka10001", {"stk_cd": "005930"})

    with pytest.raises(KiwoomRestError, match="ka10001"):
        asyncio.run(run())


def test_missing_keys_raise_auth_error():
    async def run():
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        tokens = TokenManager(http=http, api_base=BASE, app_key="", app_secret="")
        async with http:
            await tokens.get_token()

    with pytest.raises(KiwoomAuthError, match="KIWOOM_APP_KEY"):
        asyncio.run(run())
