from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.core.database import get_database_pool

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/{ticker}")
async def get_current_signal(ticker: str, pool: Any = Depends(get_database_pool)) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import SignalRepository

    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_current_by_ticker(ticker)

    if row is None:
        raise HTTPException(status_code=404, detail="Signal not found.")

    return dict(row)
