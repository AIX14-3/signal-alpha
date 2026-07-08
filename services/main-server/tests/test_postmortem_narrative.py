"""FR-7 부검 내러티브 프록시 — main-server _postmortem_narrative graceful 동작 단위 테스트.

워커 internal API 호출을 httpx 레벨에서 페이크로 대체해, 미구성/성공/에러/비활성이 전부 부검 요청을
실패시키지 않고 narrative(dict|None)로만 갈리는지 검증한다.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.api.routes.postmortem import _postmortem_narrative


def _settings(
    base_url: str = "http://worker:9000", token: str = "tok", enabled: bool = True
) -> SimpleNamespace:
    return SimpleNamespace(
        agent_worker_internal_base_url=base_url,
        internal_api_token=token,
        postmortem_narrative_enabled=enabled,
    )


class _Resp:
    def __init__(self, status_code: int, payload: dict | None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}" if payload is not None else b""

    def json(self) -> dict:
        return self._payload or {}


class _FakeClient:
    def __init__(self, resp: _Resp | None = None, raise_exc: Exception | None = None) -> None:
        self._resp = resp
        self._raise = raise_exc

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, **kwargs: object) -> _Resp:
        if self._raise is not None:
            raise self._raise
        assert self._resp is not None
        return self._resp


def _patch_client(client: _FakeClient):
    return patch(
        "app.api.routes.postmortem.httpx.AsyncClient", return_value=client
    )


class PostmortemNarrativeTest(unittest.IsolatedAsyncioTestCase):
    async def test_none_when_disabled(self):
        # 기본 off — 켜지 않으면 워커를 호출하지 않는다(헛호출/로그노이즈 방지).
        with _patch_client(_FakeClient(_Resp(200, {"narrative": {"summary": "x"}}))) as mock:
            result = await _postmortem_narrative(_settings(enabled=False), {"scope": "trade"})
        self.assertIsNone(result)
        mock.assert_not_called()

    async def test_none_when_worker_unconfigured(self):
        for settings in (_settings(base_url=""), _settings(token="")):
            result = await _postmortem_narrative(settings, {"scope": "trade"})
            self.assertIsNone(result)

    async def test_returns_narrative_dict_on_success(self):
        resp = _Resp(200, {"narrative": {"summary": "복기 요약", "key_facts": ["단서"], "model": "m"}})
        with _patch_client(_FakeClient(resp)):
            result = await _postmortem_narrative(_settings(), {"scope": "trade"})
        self.assertIsNotNone(result)
        self.assertEqual(result["summary"], "복기 요약")

    async def test_null_narrative_returns_none(self):
        resp = _Resp(200, {"narrative": None})
        with _patch_client(_FakeClient(resp)):
            result = await _postmortem_narrative(_settings(), {"scope": "trade"})
        self.assertIsNone(result)

    async def test_http_error_returns_none(self):
        resp = _Resp(500, None)
        with _patch_client(_FakeClient(resp)):
            result = await _postmortem_narrative(_settings(), {"scope": "trade"})
        self.assertIsNone(result)

    async def test_request_error_returns_none(self):
        with _patch_client(_FakeClient(raise_exc=httpx.ConnectError("down"))):
            result = await _postmortem_narrative(_settings(), {"scope": "trade"})
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
