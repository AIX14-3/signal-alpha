"""agent-worker 운영 API 프록시(main-server → worker 내부 호출)."""

from __future__ import annotations

from typing import Any

import httpx

from app.api.routes.admin_auth import admin_error
from app.core.config import Settings


async def _worker_request(
    method: str,
    path: str,
    *,
    settings: Settings,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_url = str(settings.agent_worker_internal_base_url).rstrip("/")
    headers: dict[str, str] = {}
    if settings.internal_api_token:
        headers["X-Internal-Token"] = settings.internal_api_token
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method,
                f"{base_url}{path}",
                headers=headers,
                json=json_body,
                params=params,
            )
    except httpx.RequestError as exc:
        raise admin_error(
            502,
            "WORKER_UNAVAILABLE",
            f"agent-worker 운영 API에 연결할 수 없습니다: {exc}",
        ) from None
    if response.status_code >= 400:
        raise admin_error(
            502,
            "WORKER_REQUEST_FAILED",
            f"agent-worker 운영 API 요청이 실패했습니다: {response.status_code}",
        )
    if not response.content:
        return {}
    return response.json()
