"""Load PRICE OHLCV(+수급) rows for a stock into a RawEvidence.

PRICE 도 지금까지 evidence loader 가 없던 소스다 — 기존 분석 레인은
``orchestrator/price/tasks.py`` 가 ``OhlcvReader`` 로 직접 읽는다. 이 로더는 LLM
코호트 채점 경로를 위해 같은 ``MarketDataRepository`` 조회를 다른 로더들과 같은
``RawEvidence.metadata["rows"]`` 관습으로 감싼 것이다(학습용
``app/ml/train_source_models._PriceTrainingLoader`` 의 정식화).

PIT: ``list_ohlcv_between([as_of - window, as_of])`` — look-ahead 0.
기본 창 400일(달력) = 최근 60세션 표시 + 직전 12개월 월평균(self_history)을 덮는다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.schemas.evidence import RawEvidence

DEFAULT_PRICE_WINDOW_DAYS = 400


class PriceEvidenceLoader:
    source = "PRICE"

    def __init__(self, connection: Any, *, window_days: int = DEFAULT_PRICE_WINDOW_DAYS) -> None:
        from signal_alpha_data_access.repositories import MarketDataRepository

        self._market = MarketDataRepository(connection)
        self._window_days = window_days

    async def load(
        self,
        *,
        stock_id: int,
        stock_code: str,
        as_of: date,
    ) -> list[RawEvidence]:
        start = as_of - timedelta(days=self._window_days)
        records = await self._market.list_ohlcv_between(
            stock_id=stock_id, start_date=start, end_date=as_of
        )
        rows = [dict(row) for row in records]
        latest = str(rows[-1]["trade_date"])[:10] if rows else None
        return [
            RawEvidence(
                source="PRICE",
                stock_code=stock_code,
                title=f"{stock_code} OHLCV {len(rows)}건 ({self._window_days}일 창)",
                content="",
                published_at=latest,
                metadata={
                    "rows": rows,
                    "as_of": as_of.isoformat(),
                    "window_days": self._window_days,
                    "count": len(rows),
                    "source_name": "OHLCV",
                },
            )
        ]
