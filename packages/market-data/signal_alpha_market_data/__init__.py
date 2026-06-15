"""Signal Alpha 공용 시장 데이터 추상계층.

소스(키움/토스/DART)별 호출을 한 프로토콜 뒤로 숨겨, 메인 서비스 price collector와
harness/ai_tech_backtest가 같은 계약을 공유하게 한다.

- contracts: DTO + MarketDataSource 프로토콜 (순수 계약, I/O 없음)
- valuation: PER/PBR 패스스루 + PSR 파생 계산 (순수 함수)

소스 구현(KiwoomSource/TossSource)은 httpx 의존이 있어 별도 모듈에서 이 계약을 구현한다.
"""

from __future__ import annotations

from signal_alpha_market_data.contracts import (
    Candle,
    ExchangeRate,
    MarketDataSource,
    PriceSnapshot,
    ValuationMetrics,
)
from signal_alpha_market_data.valuation import (
    EOK_TO_KRW_MILLION,
    build_valuation,
    compute_psr,
)

__all__ = [
    "Candle",
    "ExchangeRate",
    "MarketDataSource",
    "PriceSnapshot",
    "ValuationMetrics",
    "EOK_TO_KRW_MILLION",
    "build_valuation",
    "compute_psr",
]
