from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends

from app.analyzers.price.analyzer import PriceAnalyzer
from app.collectors.price.ohlcv_reader import OhlcvReader
from app.core.database import get_database_pool
from app.orchestrator.pipeline import SourcePipeline

router = APIRouter(prefix="/internal/price", tags=["price"])

PricePipelineFactory = Callable[[Any], SourcePipeline]


def build_price_pipeline(connection: Any) -> SourcePipeline:
    from signal_alpha_data_access.repositories import MarketDataRepository, StockRepository

    return SourcePipeline(
        source="PRICE",
        collector=OhlcvReader(
            stocks=StockRepository(connection),
            market_data=MarketDataRepository(connection),
        ),
        analyzer=PriceAnalyzer(),
    )


def get_price_pipeline_factory() -> PricePipelineFactory:
    return build_price_pipeline


@router.post("/analyze/{stock_code}")
async def analyze_price(
    stock_code: str,
    pool: Any = Depends(get_database_pool),
    pipeline_factory: PricePipelineFactory = Depends(get_price_pipeline_factory),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        pipeline = pipeline_factory(connection)
        result = await pipeline.run(stock_code)
    return asdict(result)
