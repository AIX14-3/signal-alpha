from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.core.database import get_database_pool
from app.quant.runner import run_daily, scorecard_from_db_row

router = APIRouter(prefix="/internal/quant", tags=["quant"])


@router.post("/run-daily")
async def run_quant_daily(pool: Any = Depends(get_database_pool)) -> dict[str, Any]:
    """유니버스 횡단면 점수 일일 배치 (같은 날짜 재실행은 멱등 upsert)."""
    return await run_daily(pool)


@router.get("/score/{stock_code}")
async def get_quant_score(
    stock_code: str, pool: Any = Depends(get_database_pool)
) -> dict[str, Any]:
    from signal_alpha_data_access.repositories.scoring import ScoringRepository

    row = await ScoringRepository(pool).get_latest_quant_score(stock_code)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no quant score for {stock_code}")
    card = scorecard_from_db_row(row)
    return {**card.to_dict(), "analysis_date": str(row["analysis_date"])}
