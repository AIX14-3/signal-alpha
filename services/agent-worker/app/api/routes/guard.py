"""guard 내부 라우트 — 수동 검증/운영용 1회 실행 트리거 + 런타임 상태."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.config import get_settings

router = APIRouter(prefix="/internal/guard", tags=["guard"])


@router.post("/run")
async def run_guard_once(request: Request) -> dict[str, Any]:
    """감시 사이클 1회 실행. guard_* 는 backend DB 소유 — backend 풀이 필수."""
    pool = getattr(request.app.state, "backend_database_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "BACKEND_POOL_NOT_CONFIGURED",
                "message": "BACKEND_DATABASE_URL is not set; guard cannot run",
            },
        )
    from app.guard.daemon import run_guard_cycle

    return await run_guard_cycle(pool, get_settings())


@router.get("/status")
async def get_guard_runtime_status(request: Request) -> dict[str, Any]:
    status = getattr(request.app.state, "guard_status", None)
    return {"enabled": get_settings().guard_enabled, "runtime": status.snapshot() if status else None}
